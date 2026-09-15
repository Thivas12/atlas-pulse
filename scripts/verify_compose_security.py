"""Verify a rendered Compose model against the reviewed runtime trust graph."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

STRONG_URL_SAFE_VALUE = re.compile(r"[A-Za-z0-9_-]{32,128}")
LOCAL_RATE_LIMIT_SECRET = "local-development-only-rate-limit-secret"
PublishedPort = tuple[str, int, int, str]


def _mapping(value: object, label: str, errors: list[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        errors.append(f"{label} must be an object")
        return {}
    return cast(dict[str, object], value)


def _string_set(value: object, label: str, errors: list[str]) -> set[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not all(isinstance(item, str) for item in value)
    ):
        errors.append(f"{label} must be an array of strings")
        return set()
    return set(cast(Sequence[str], value))


def _service_networks(service: Mapping[str, object], label: str, errors: list[str]) -> set[str]:
    value = service.get("networks", {})
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            errors.append(f"{label}.networks must have string keys")
            return set()
        return set(cast(dict[str, object], value))
    return _string_set(value, f"{label}.networks", errors)


def _network_gateway_priority(
    service: Mapping[str, object], network_name: str, label: str, errors: list[str]
) -> int:
    value = service.get("networks", {})
    if not isinstance(value, dict):
        return 0
    networks = cast(dict[str, object], value)
    connection = networks.get(network_name)
    if connection is None:
        return 0
    configuration = _mapping(connection, f"{label}.networks.{network_name}", errors)
    priority = configuration.get("gw_priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        errors.append(f"{label}.networks.{network_name}.gw_priority must be an integer")
        return 0
    return priority


def _environment(
    service: Mapping[str, object], label: str, errors: list[str]
) -> Mapping[str, object]:
    return _mapping(service.get("environment", {}), f"{label}.environment", errors)


def _port_number(value: object, label: str, errors: list[str]) -> int | None:
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        errors.append(f"{label} must be an integer from 1 through 65535")
        return None
    return value


def _published_ports(
    service: Mapping[str, object], label: str, errors: list[str]
) -> set[PublishedPort]:
    value = service.get("ports", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        errors.append(f"{label}.ports must be an array")
        return set()
    publications: set[PublishedPort] = set()
    for index, raw_publication in enumerate(value):
        port_label = f"{label}.ports[{index}]"
        publication = _mapping(raw_publication, port_label, errors)
        host_ip = publication.get("host_ip", "*")
        if not isinstance(host_ip, str):
            errors.append(f"{port_label}.host_ip must be a string")
            continue
        if host_ip in {"", "0.0.0.0", "::"}:
            host_ip = "*"
        published = _port_number(publication.get("published"), f"{port_label}.published", errors)
        target = _port_number(publication.get("target"), f"{port_label}.target", errors)
        protocol = publication.get("protocol", "tcp")
        if not isinstance(protocol, str) or protocol not in {"tcp", "udp"}:
            errors.append(f"{port_label}.protocol must be tcp or udp")
            continue
        mode = publication.get("mode", "ingress")
        if mode != "ingress":
            errors.append(f"{port_label}.mode must be ingress")
            continue
        if published is None or target is None:
            continue
        normalized = (host_ip, published, target, protocol)
        if normalized in publications:
            errors.append(f"{port_label} duplicates another published port")
        publications.add(normalized)
    return publications


def _database_errors(
    services: Mapping[str, object],
    database_clients: set[str],
    *,
    require_public_password: bool,
    errors: list[str],
) -> None:
    postgres = _mapping(services.get("postgres"), "services.postgres", errors)
    postgres_environment = _environment(postgres, "services.postgres", errors)
    password_value = postgres_environment.get("POSTGRES_PASSWORD")
    if not isinstance(password_value, str):
        errors.append("services.postgres.POSTGRES_PASSWORD must be a string")
        return
    if require_public_password and STRONG_URL_SAFE_VALUE.fullmatch(password_value) is None:
        errors.append(
            "public POSTGRES_PASSWORD must be 32-128 URL-safe ASCII letters, digits, '_' or '-'"
        )

    for client_name in sorted(database_clients):
        client = _mapping(services.get(client_name), f"services.{client_name}", errors)
        environment = _environment(client, f"services.{client_name}", errors)
        database_url = environment.get("ATLAS_DATABASE_URL")
        if not isinstance(database_url, str):
            errors.append(f"services.{client_name}.ATLAS_DATABASE_URL must be a string")
            continue
        try:
            parsed = urlsplit(database_url)
            port = parsed.port
        except ValueError:
            errors.append(f"services.{client_name}.ATLAS_DATABASE_URL is malformed")
            continue
        if (
            parsed.scheme != "postgresql+asyncpg"
            or parsed.username != "atlas"
            or parsed.hostname != "postgres"
            or port != 5432
            or parsed.path != "/atlas"
        ):
            errors.append(
                f"services.{client_name}.ATLAS_DATABASE_URL must target atlas@postgres:5432/atlas"
            )
        client_password = unquote(parsed.password or "")
        if not secrets.compare_digest(client_password, password_value):
            errors.append(
                f"services.{client_name}.ATLAS_DATABASE_URL password does not match PostgreSQL"
            )


def _rate_limit_errors(
    services: Mapping[str, object], *, require_public_secret: bool, errors: list[str]
) -> None:
    api = _mapping(services.get("api"), "services.api", errors)
    environment = _environment(api, "services.api", errors)
    secret = environment.get("ATLAS_API_RATE_LIMIT_CLIENT_SECRET")
    if not isinstance(secret, str):
        errors.append("services.api.ATLAS_API_RATE_LIMIT_CLIENT_SECRET must be a string")
    elif require_public_secret:
        if STRONG_URL_SAFE_VALUE.fullmatch(secret) is None or secret == LOCAL_RATE_LIMIT_SECRET:
            errors.append(
                "public ATLAS_API_RATE_LIMIT_CLIENT_SECRET must be a non-default 32-128 "
                "character URL-safe value"
            )
        postgres = _mapping(services.get("postgres"), "services.postgres", errors)
        postgres_environment = _environment(postgres, "services.postgres", errors)
        password = postgres_environment.get("POSTGRES_PASSWORD")
        if isinstance(password, str) and secrets.compare_digest(secret, password):
            errors.append(
                "public ATLAS_API_RATE_LIMIT_CLIENT_SECRET must differ from POSTGRES_PASSWORD"
            )


def validate_compose_model(
    model: Mapping[str, object], policy: Mapping[str, object], deployment: str
) -> list[str]:
    """Return every trust-graph violation in a rendered Compose model."""

    errors: list[str] = []
    deployments = _mapping(policy.get("deployments"), "policy.deployments", errors)
    deployment_policy = _mapping(
        deployments.get(deployment), f"policy.deployments.{deployment}", errors
    )
    expected_networks = _mapping(
        deployment_policy.get("networks"), f"policy.deployments.{deployment}.networks", errors
    )
    services = _mapping(model.get("services"), "services", errors)
    networks = _mapping(model.get("networks"), "networks", errors)

    expected_service_names: set[str] = set()
    expected_members_by_network: dict[str, set[str]] = {}
    for network_name, raw_specification in expected_networks.items():
        specification = _mapping(
            raw_specification,
            f"policy.deployments.{deployment}.networks.{network_name}",
            errors,
        )
        members = _string_set(
            specification.get("services"),
            f"policy.deployments.{deployment}.networks.{network_name}.services",
            errors,
        )
        expected_members_by_network[network_name] = members
        expected_service_names.update(members)

    if set(services) != expected_service_names:
        errors.append(
            f"services differ: expected {sorted(expected_service_names)}, got {sorted(services)}"
        )
    if set(networks) != set(expected_networks):
        errors.append(
            f"networks differ: expected {sorted(expected_networks)}, got {sorted(networks)}"
        )

    actual_members_by_network: dict[str, set[str]] = {name: set() for name in networks}
    for service_name, raw_service in services.items():
        service = _mapping(raw_service, f"services.{service_name}", errors)
        for network_name in _service_networks(service, f"services.{service_name}", errors):
            actual_members_by_network.setdefault(network_name, set()).add(service_name)

    for network_name, expected_members in expected_members_by_network.items():
        specification = _mapping(
            expected_networks.get(network_name),
            f"policy.deployments.{deployment}.networks.{network_name}",
            errors,
        )
        expected_internal = specification.get("internal")
        if not isinstance(expected_internal, bool):
            errors.append(
                f"policy.deployments.{deployment}.networks.{network_name}.internal must be boolean"
            )
            continue
        actual_network = _mapping(networks.get(network_name), f"networks.{network_name}", errors)
        actual_internal = actual_network.get("internal", False) is True
        if actual_internal != expected_internal:
            errors.append(
                f"network {network_name} internal={actual_internal}; expected {expected_internal}"
            )
        actual_members = actual_members_by_network.get(network_name, set())
        if actual_members != expected_members:
            errors.append(
                f"network {network_name} members differ: expected {sorted(expected_members)}, "
                f"got {sorted(actual_members)}"
            )
        expected_gateway_priority = specification.get("gateway_priority")
        if expected_gateway_priority is not None:
            if not isinstance(expected_gateway_priority, int) or isinstance(
                expected_gateway_priority, bool
            ):
                errors.append(
                    f"policy.deployments.{deployment}.networks.{network_name}."
                    "gateway_priority must be an integer"
                )
                continue
            for service_name in sorted(expected_members):
                service = _mapping(services.get(service_name), f"services.{service_name}", errors)
                actual_gateway_priority = _network_gateway_priority(
                    service, network_name, f"services.{service_name}", errors
                )
                if actual_gateway_priority != expected_gateway_priority:
                    errors.append(
                        f"services.{service_name}.networks.{network_name}.gw_priority="
                        f"{actual_gateway_priority}; expected {expected_gateway_priority}"
                    )

    environment_owners = _mapping(
        deployment_policy.get("environment_owners"),
        f"policy.deployments.{deployment}.environment_owners",
        errors,
    )
    parsed_environments = {
        service_name: _environment(
            _mapping(raw_service, f"services.{service_name}", errors),
            f"services.{service_name}",
            errors,
        )
        for service_name, raw_service in services.items()
    }
    for variable, raw_expected_owners in environment_owners.items():
        expected_owners = _string_set(
            raw_expected_owners,
            f"policy.deployments.{deployment}.environment_owners.{variable}",
            errors,
        )
        actual_owners = {
            service_name
            for service_name, environment in parsed_environments.items()
            if variable in environment
        }
        if actual_owners != expected_owners:
            errors.append(
                f"{variable} owners differ: expected {sorted(expected_owners)}, "
                f"got {sorted(actual_owners)}"
            )

    expected_publications = _mapping(
        deployment_policy.get("published_ports"),
        f"policy.deployments.{deployment}.published_ports",
        errors,
    )
    unknown_port_owners = set(expected_publications) - set(services)
    if unknown_port_owners:
        errors.append(
            "published port owners are not services: " + ", ".join(sorted(unknown_port_owners))
        )
    for service_name, raw_service in services.items():
        service = _mapping(raw_service, f"services.{service_name}", errors)
        actual_ports = _published_ports(service, f"services.{service_name}", errors)
        expected_ports = _published_ports(
            {"ports": expected_publications.get(service_name, [])},
            f"policy.deployments.{deployment}.published_ports.{service_name}",
            errors,
        )
        if actual_ports != expected_ports:
            errors.append(
                f"services.{service_name} published ports differ: "
                f"expected {sorted(expected_ports)}, got {sorted(actual_ports)}"
            )

    database_clients = _string_set(
        environment_owners.get("ATLAS_DATABASE_URL"),
        f"policy.deployments.{deployment}.environment_owners.ATLAS_DATABASE_URL",
        errors,
    )
    require_public_password = deployment_policy.get("require_public_database_password")
    if not isinstance(require_public_password, bool):
        errors.append(
            f"policy.deployments.{deployment}.require_public_database_password must be boolean"
        )
    else:
        _database_errors(
            services,
            database_clients,
            require_public_password=require_public_password,
            errors=errors,
        )
    require_public_rate_limit_secret = deployment_policy.get("require_public_rate_limit_secret")
    if not isinstance(require_public_rate_limit_secret, bool):
        errors.append(
            f"policy.deployments.{deployment}.require_public_rate_limit_secret must be boolean"
        )
    else:
        _rate_limit_errors(
            services,
            require_public_secret=require_public_rate_limit_secret,
            errors=errors,
        )
    return errors


def _load_object(path: Path) -> Mapping[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, object], value)


def main() -> int:
    """Validate one JSON Compose model read from standard input."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--deployment",
        required=True,
        choices=("base", "free-tier", "workstation-funnel"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("deploy/compose-security-policy.json"),
    )
    args = parser.parse_args()
    try:
        model_value: object = json.load(sys.stdin)
        if not isinstance(model_value, dict) or not all(
            isinstance(key, str) for key in model_value
        ):
            raise ValueError("standard input must contain a JSON object")
        model = cast(dict[str, object], model_value)
        policy = _load_object(args.policy)
    except (OSError, json.JSONDecodeError, ValueError) as load_error:
        print(f"compose security validation error: {load_error}", file=sys.stderr)
        return 2

    errors = validate_compose_model(model, policy, args.deployment)
    if errors:
        for violation in errors:
            print(f"compose security policy violation: {violation}", file=sys.stderr)
        return 1
    print(f"Compose security policy passed for {args.deployment}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
