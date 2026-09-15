"""Tests for the rendered Compose trust-graph policy."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DEPLOYMENTS = ("free-tier", "workstation-funnel")
POLICY = cast(
    dict[str, Any],
    json.loads((ROOT / "deploy" / "compose-security-policy.json").read_text(encoding="utf-8")),
)


def _compliant_model(deployment: str) -> dict[str, Any]:
    deployment_policy = POLICY["deployments"][deployment]
    services: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    for network_name, specification in deployment_policy["networks"].items():
        networks[network_name] = {"internal": specification["internal"]}
        for service_name in specification["services"]:
            service = services.setdefault(service_name, {"networks": {}, "environment": {}})
            connection = {}
            if "gateway_priority" in specification:
                connection["gw_priority"] = specification["gateway_priority"]
            service["networks"][network_name] = connection

    for service_name, publications in deployment_policy["published_ports"].items():
        services[service_name]["ports"] = copy.deepcopy(publications)

    password = "atlas" if deployment == "base" else "a1_" * 16
    values = {
        "ATLAS_AGENT_APPROVAL_TRUSTED_KEY_IDS": "[]",
        "ATLAS_API_EXPENSIVE_RATE_LIMIT_REQUESTS": "20",
        "ATLAS_API_RATE_LIMIT_CLIENT_SECRET": "rate_limit_client_secret_value_123456",
        "ATLAS_API_RATE_LIMIT_REQUESTS": "120",
        "ATLAS_API_RATE_LIMIT_WINDOW_SECONDS": "60",
        "ATLAS_DATABASE_URL": (f"postgresql+asyncpg://atlas:{password}@postgres:5432/atlas"),
        "ATLAS_FIRMS_MAP_KEY": "",
        "ATLAS_PUBLIC_HOST": "atlas.example.test",
        "ATLAS_VALKEY_URL": "valkey://valkey:6379/0",
        "POSTGRES_PASSWORD": password,
    }
    for variable, owners in deployment_policy["environment_owners"].items():
        for owner in owners:
            services[owner]["environment"][variable] = values[variable]
    return {"services": services, "networks": networks}


def _validate(model: dict[str, Any], deployment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify_compose_security.py", "--deployment", deployment],
        cwd=ROOT,
        input=json.dumps(model),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("deployment", ["base", *PUBLIC_DEPLOYMENTS])
def test_reviewed_compose_models_pass(deployment: str) -> None:
    result = _validate(_compliant_model(deployment), deployment)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"Compose security policy passed for {deployment}.\n"


def test_unreviewed_network_path_is_rejected() -> None:
    model = _compliant_model("base")
    model["services"]["api"]["networks"]["source-egress"] = None

    result = _validate(model, "base")

    assert result.returncode == 1
    assert "source-egress members differ" in result.stderr


def test_workstation_funnel_edge_must_remain_loopback_only() -> None:
    model = _compliant_model("workstation-funnel")
    model["services"]["edge"]["ports"][0]["host_ip"] = "0.0.0.0"

    result = _validate(model, "workstation-funnel")

    assert result.returncode == 1
    assert "services.edge published ports differ" in result.stderr


def test_unreviewed_published_port_is_rejected() -> None:
    model = _compliant_model("base")
    model["services"]["ingestor"]["ports"] = [
        {"host_ip": "127.0.0.1", "published": 9000, "target": 9000, "protocol": "tcp"}
    ]

    result = _validate(model, "base")

    assert result.returncode == 1
    assert "services.ingestor published ports differ" in result.stderr


def test_egress_must_be_the_default_gateway() -> None:
    model = _compliant_model("free-tier")
    model["services"]["edge"]["networks"]["edge-egress"].pop("gw_priority")

    result = _validate(model, "free-tier")

    assert result.returncode == 1
    assert "services.edge.networks.edge-egress.gw_priority=0; expected 1" in result.stderr


def test_database_credential_exposure_is_rejected() -> None:
    model = _compliant_model("base")
    model["services"]["ingestor"]["environment"]["ATLAS_DATABASE_URL"] = (
        "postgresql+asyncpg://atlas:atlas@postgres:5432/atlas"
    )

    result = _validate(model, "base")

    assert result.returncode == 1
    assert "ATLAS_DATABASE_URL owners differ" in result.stderr


@pytest.mark.parametrize("deployment", PUBLIC_DEPLOYMENTS)
def test_public_database_password_must_be_strong_and_url_safe(deployment: str) -> None:
    model = _compliant_model(deployment)
    model["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = "atlas"

    result = _validate(model, deployment)

    assert result.returncode == 1
    assert "public POSTGRES_PASSWORD must be 32-128" in result.stderr


@pytest.mark.parametrize("deployment", PUBLIC_DEPLOYMENTS)
def test_database_clients_must_use_the_server_password(deployment: str) -> None:
    model = copy.deepcopy(_compliant_model(deployment))
    model["services"]["api"]["environment"]["ATLAS_DATABASE_URL"] = (
        "postgresql+asyncpg://atlas:different_password_value_123456@postgres:5432/atlas"
    )

    result = _validate(model, deployment)

    assert result.returncode == 1
    assert "services.api.ATLAS_DATABASE_URL password does not match" in result.stderr


@pytest.mark.parametrize("deployment", PUBLIC_DEPLOYMENTS)
def test_public_rate_limit_secret_must_be_strong_and_url_safe(deployment: str) -> None:
    model = _compliant_model(deployment)
    model["services"]["api"]["environment"]["ATLAS_API_RATE_LIMIT_CLIENT_SECRET"] = "short"

    result = _validate(model, deployment)

    assert result.returncode == 1
    assert "public ATLAS_API_RATE_LIMIT_CLIENT_SECRET must be a non-default 32-128" in result.stderr


@pytest.mark.parametrize("deployment", PUBLIC_DEPLOYMENTS)
def test_public_rate_limit_secret_must_not_reuse_database_password(deployment: str) -> None:
    model = _compliant_model(deployment)
    model["services"]["api"]["environment"]["ATLAS_API_RATE_LIMIT_CLIENT_SECRET"] = model[
        "services"
    ]["postgres"]["environment"]["POSTGRES_PASSWORD"]

    result = _validate(model, deployment)

    assert result.returncode == 1
    assert "must differ from POSTGRES_PASSWORD" in result.stderr


def test_rate_limit_secret_exposure_is_rejected() -> None:
    model = _compliant_model("base")
    model["services"]["web"]["environment"]["ATLAS_API_RATE_LIMIT_CLIENT_SECRET"] = (
        "rate_limit_client_secret_value_123456"
    )

    result = _validate(model, "base")

    assert result.returncode == 1
    assert "ATLAS_API_RATE_LIMIT_CLIENT_SECRET owners differ" in result.stderr
