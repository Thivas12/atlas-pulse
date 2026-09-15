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

    password = "atlas" if deployment == "base" else "a1_" * 16
    values = {
        "ATLAS_AGENT_APPROVAL_TRUSTED_KEY_IDS": "[]",
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


@pytest.mark.parametrize("deployment", ["base", "free-tier"])
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


def test_public_database_password_must_be_strong_and_url_safe() -> None:
    model = _compliant_model("free-tier")
    model["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] = "atlas"

    result = _validate(model, "free-tier")

    assert result.returncode == 1
    assert "public POSTGRES_PASSWORD must be 32-128" in result.stderr


def test_database_clients_must_use_the_server_password() -> None:
    model = copy.deepcopy(_compliant_model("free-tier"))
    model["services"]["api"]["environment"]["ATLAS_DATABASE_URL"] = (
        "postgresql+asyncpg://atlas:different_password_value_123456@postgres:5432/atlas"
    )

    result = _validate(model, "free-tier")

    assert result.returncode == 1
    assert "services.api.ATLAS_DATABASE_URL password does not match" in result.stderr
