"""Create a one-time secret-bearing environment file for the workstation Funnel profile."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_HOST = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
MANAGED_KEYS = (
    "ATLAS_API_RATE_LIMIT_CLIENT_SECRET",
    "ATLAS_BUILD_COMMIT_SHA",
    "ATLAS_ENVIRONMENT",
    "ATLAS_POSTGRES_PASSWORD",
    "ATLAS_PUBLIC_HOST",
)
ASSIGNMENT = re.compile(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")


def normalize_public_host(value: str) -> str:
    """Return one normalized Tailscale device DNS name or raise."""

    host = value.strip().removesuffix(".").lower()
    tailscale_prefix = host.removesuffix(".ts.net")
    if (
        not host.endswith(".ts.net")
        or "." not in tailscale_prefix
        or PUBLIC_HOST.fullmatch(host) is None
    ):
        raise ValueError(
            "public host must be the device's full Tailscale DNS name ending in .ts.net"
        )
    return host


def render_environment(template: str, replacements: dict[str, str]) -> str:
    """Replace each managed assignment exactly once without exposing values to a shell."""

    if set(replacements) != set(MANAGED_KEYS):
        raise ValueError("environment replacements must contain the exact managed key set")
    seen: set[str] = set()
    output: list[str] = []
    for line in template.splitlines():
        match = ASSIGNMENT.fullmatch(line)
        key = match.group(1) if match is not None else None
        if key not in replacements:
            output.append(line)
            continue
        if key in seen:
            raise ValueError(f"template contains duplicate managed key {key}")
        output.append(f"{key}={replacements[key]}")
        seen.add(key)
    missing = set(replacements) - seen
    if missing:
        raise ValueError(f"template is missing managed keys: {', '.join(sorted(missing))}")
    return "\n".join(output) + "\n"


def _reviewed_commit() -> str:
    revision = subprocess.run(
        ("git", "rev-parse", "--verify", "HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Git HEAD is not a full commit SHA")
    status = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=no"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise ValueError("tracked files differ from the checked-out commit")
    return revision


def _write_new_private_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> int:
    """Generate public-profile secrets and bind them to the exact clean checkout."""

    parser = argparse.ArgumentParser(
        description=(
            "Create a private .env for the reviewed workstation Funnel deployment. "
            "The command refuses to overwrite an existing file."
        )
    )
    parser.add_argument("--public-host", required=True)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()

    try:
        host = normalize_public_host(args.public_host)
        revision = _reviewed_commit()
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        content = render_environment(
            template,
            {
                "ATLAS_API_RATE_LIMIT_CLIENT_SECRET": secrets.token_hex(32),
                "ATLAS_BUILD_COMMIT_SHA": revision,
                "ATLAS_ENVIRONMENT": "workstation-funnel-public",
                "ATLAS_POSTGRES_PASSWORD": secrets.token_hex(32),
                "ATLAS_PUBLIC_HOST": host,
            },
        )
        _write_new_private_file(args.output, content)
    except (FileExistsError, OSError, subprocess.CalledProcessError, ValueError) as error:
        parser.error(str(error))

    print(f"Wrote private deployment settings to {args.output} for commit {revision}.")
    print(
        "No secret value was printed. Keep this file out of source control and diagnostic bundles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
