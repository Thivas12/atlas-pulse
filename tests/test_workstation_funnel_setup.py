"""Tests for the no-card workstation Funnel setup helper."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "configure_workstation_funnel.py")
)
MANAGED_KEYS = cast(tuple[str, ...], MODULE["MANAGED_KEYS"])
normalize_public_host = cast(Callable[[str], str], MODULE["normalize_public_host"])
render_environment = cast(Callable[[str, dict[str, str]], str], MODULE["render_environment"])
write_new_private_file = cast(Callable[[Path, str], None], MODULE["_write_new_private_file"])


def test_public_host_is_normalized_without_weakening_the_tailscale_boundary() -> None:
    assert (
        normalize_public_host("  Atlas-PC.Example-Tailnet.ts.net.  ")
        == "atlas-pc.example-tailnet.ts.net"
    )

    for invalid in (
        "https://atlas-pc.example-tailnet.ts.net",
        "atlas-pc.example.com",
        "atlas_pc.example-tailnet.ts.net",
        "atlas-pc.ts.net",
        "ts.net",
    ):
        with pytest.raises(ValueError, match="full Tailscale DNS name"):
            normalize_public_host(invalid)


def test_environment_render_replaces_all_public_values_once() -> None:
    template = """ATLAS_ENVIRONMENT=development
ATLAS_BUILD_COMMIT_SHA=unknown
ATLAS_API_RATE_LIMIT_CLIENT_SECRET=local-development-only-rate-limit-secret
# ATLAS_PUBLIC_HOST=example.invalid
# ATLAS_POSTGRES_PASSWORD=replace-me
ATLAS_GDELT_ENABLED=true
"""
    replacements = {
        "ATLAS_API_RATE_LIMIT_CLIENT_SECRET": "a" * 64,
        "ATLAS_BUILD_COMMIT_SHA": "1" * 40,
        "ATLAS_ENVIRONMENT": "workstation-funnel-public",
        "ATLAS_POSTGRES_PASSWORD": "b" * 64,
        "ATLAS_PUBLIC_HOST": "atlas-pc.example-tailnet.ts.net",
    }

    rendered = render_environment(template, replacements)

    for key in MANAGED_KEYS:
        assert rendered.count(f"{key}=") == 1
        assert f"{key}={replacements[key]}" in rendered
    assert "local-development-only-rate-limit-secret" not in rendered
    assert "ATLAS_GDELT_ENABLED=true" in rendered


def test_environment_render_rejects_missing_or_duplicate_managed_keys() -> None:
    replacements = {key: "value" for key in MANAGED_KEYS}

    with pytest.raises(ValueError, match="missing managed keys"):
        render_environment("ATLAS_ENVIRONMENT=development\n", replacements)
    with pytest.raises(ValueError, match="duplicate managed key ATLAS_ENVIRONMENT"):
        render_environment(
            "\n".join(f"{key}=one" for key in MANAGED_KEYS) + "\nATLAS_ENVIRONMENT=two\n",
            replacements,
        )


def test_private_environment_file_is_not_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / ".env"

    write_new_private_file(destination, "first\n")

    assert destination.read_text(encoding="utf-8") == "first\n"
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_new_private_file(destination, "second\n")
    assert destination.read_text(encoding="utf-8") == "first\n"
