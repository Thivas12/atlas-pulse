"""Static security policy for repository-owned GitHub workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = ROOT / ".github" / "workflows"
REMOTE_ACTION = re.compile(
    r"^\s*uses:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
IMMUTABLE_IMAGE_REFERENCE = re.compile(
    r"[a-z0-9][a-z0-9./_-]*:[A-Za-z0-9][A-Za-z0-9_.-]*@sha256:[0-9a-f]{64}"
)


def _workflows() -> tuple[Path, ...]:
    return tuple(sorted((*WORKFLOW_DIRECTORY.glob("*.yml"), *WORKFLOW_DIRECTORY.glob("*.yaml"))))


def test_remote_actions_are_commit_pinned_and_checkout_drops_credentials() -> None:
    workflows = _workflows()
    assert workflows

    for workflow in workflows:
        content = workflow.read_text(encoding="utf-8")
        references = REMOTE_ACTION.findall(content)
        for action, revision in references:
            assert FULL_COMMIT_SHA.fullmatch(revision), (
                f"{workflow.name}: {action} must be pinned to a full lowercase commit SHA"
            )

        lines = content.splitlines()
        for index, line in enumerate(lines):
            if "uses: actions/checkout@" not in line:
                continue
            checkout_block = "\n".join(lines[index + 1 : index + 5])
            assert re.search(
                r"^\s+persist-credentials:\s+false\s*$", checkout_block, re.MULTILINE
            ), f"{workflow.name}: checkout must not retain the GitHub token"


def test_workflows_default_to_read_only_and_avoid_privileged_pr_trigger() -> None:
    for workflow in _workflows():
        content = workflow.read_text(encoding="utf-8")
        assert "pull_request_target:" not in content
        assert re.search(r"^permissions:\n  contents: read\s*$", content, re.MULTILINE), (
            f"{workflow.name}: declare a read-only workflow token"
        )

        write_permissions = re.findall(r"^\s+([a-z-]+):\s+write\s*$", content, re.MULTILINE)
        expected = ["security-events"] if workflow.name == "security.yml" else []
        assert write_permissions == expected


def test_security_workflow_covers_source_and_dependency_changes() -> None:
    content = (WORKFLOW_DIRECTORY / "security.yml").read_text(encoding="utf-8")

    assert "- python" in content
    assert "- javascript-typescript" in content
    assert "queries: security-extended" in content
    assert "run: uv audit --frozen" in content
    assert "run: npm audit --package-lock-only --audit-level=moderate" in content


def test_container_inputs_are_digest_pinned_synchronized_and_scanned() -> None:
    images = json.loads((ROOT / "deploy" / "container-images.json").read_text(encoding="utf-8"))
    assert set(images) == {
        "caddy",
        "dockerfile_frontend",
        "node",
        "postgis",
        "python",
        "uv",
        "valkey",
    }
    assert len(set(images.values())) == len(images)
    for name, reference in images.items():
        assert IMMUTABLE_IMAGE_REFERENCE.fullmatch(reference), (
            f"{name}: image must include a readable tag and immutable SHA-256 digest"
        )

    expected_dockerfile_inputs = {
        "Dockerfile": {
            images["dockerfile_frontend"],
            images["python"],
            images["uv"],
        },
        "web/Dockerfile": {
            images["caddy"],
            images["dockerfile_frontend"],
            images["node"],
        },
        "docker/postgres/Dockerfile": {
            images["dockerfile_frontend"],
            images["postgis"],
        },
        "deploy/free-tier/Dockerfile": {
            images["caddy"],
            images["dockerfile_frontend"],
        },
    }
    for relative_path, expected in expected_dockerfile_inputs.items():
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        declared = set(re.findall(r"^(?:# syntax=|FROM\s+)([^\s]+)", content, re.MULTILINE))
        assert declared == expected, f"{relative_path}: synchronize inputs with the image contract"

    api_dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "UV_NO_CACHE=1" in api_dockerfile
    assert "apt-get upgrade --yes" in api_dockerfile
    for relative_path in (
        "web/Dockerfile",
        "docker/postgres/Dockerfile",
        "deploy/free-tier/Dockerfile",
    ):
        assert "apk upgrade --no-cache" in (ROOT / relative_path).read_text(encoding="utf-8")

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    free_tier = (ROOT / "deploy" / "free-tier" / "compose.yaml").read_text(encoding="utf-8")
    ci_workflow = (WORKFLOW_DIRECTORY / "ci.yml").read_text(encoding="utf-8")
    assert f"image: {images['valkey']}" in compose
    assert "dockerfile: deploy/free-tier/Dockerfile" in free_tier
    assert "image: atlas-pulse-edge:2.11.4-alpine" in free_tier
    assert f"image: {images['valkey']}" in ci_workflow

    allowed_yaml_images = {
        images["valkey"],
        "atlas-pulse-edge:2.11.4-alpine",
        "atlas-pulse-postgres:17-postgis3.5-pgvector0.8.6-alpine",
    }
    for relative_path in (
        "compose.yaml",
        "deploy/free-tier/compose.yaml",
        ".github/workflows/ci.yml",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        declared_images = re.findall(r"^\s+image:\s+([^\s]+)", content, re.MULTILINE)
        assert set(declared_images) <= allowed_yaml_images, (
            f"{relative_path}: external images must use the immutable image contract"
        )

    security_workflow = (WORKFLOW_DIRECTORY / "security.yml").read_text(encoding="utf-8")
    report_step = "- name: Report all high and critical runtime findings"
    gate_step = "- name: Reject fixable high and critical operating-system findings"
    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in (
        security_workflow
    )
    assert "version: v0.74.0" in security_workflow
    assert "cache: false" in security_workflow
    assert 'open("deploy/container-images.json")' in security_workflow
    assert security_workflow.count("--severity HIGH,CRITICAL") == 2
    assert security_workflow.count("--exit-code 0") == 1
    assert security_workflow.count("--pkg-types os") == 1
    assert security_workflow.count("--ignore-unfixed") == 1
    assert security_workflow.count("--exit-code 1") == 1
    for runtime_image in (
        '"atlas-pulse-api:${{ github.sha }}"',
        '"atlas-pulse-web:${{ github.sha }}"',
        '"atlas-pulse-edge:${{ github.sha }}"',
        '"atlas-pulse-postgres:${{ github.sha }}"',
    ):
        assert security_workflow.count(runtime_image) == 3
    assert security_workflow.count('open("deploy/container-images.json")') == 3
    assert "scan_status=0" in security_workflow
    assert "|| scan_status=1" in security_workflow
    assert 'exit "$scan_status"' in security_workflow
    assert report_step in security_workflow
    assert gate_step in security_workflow
    assert security_workflow.index(report_step) < security_workflow.index(gate_step)

    dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    for directory in ("/", "/web", "/docker/postgres", "/deploy/free-tier"):
        assert re.search(
            rf"package-ecosystem: docker\n\s+directory: {re.escape(directory)}\s*$",
            dependabot,
            re.MULTILINE,
        )


def test_compose_trust_graph_is_machine_checked() -> None:
    policy = json.loads(
        (ROOT / "deploy" / "compose-security-policy.json").read_text(encoding="utf-8")
    )
    assert policy["schema_version"] == "1.0.0"
    assert set(policy["deployments"]) == {"base", "free-tier"}

    for deployment in policy["deployments"].values():
        networks = deployment["networks"]
        assert all(
            specification["internal"] or len(specification["services"]) == 1
            for specification in networks.values()
        )
        assert deployment["environment_owners"]["ATLAS_FIRMS_MAP_KEY"] == ["ingestor"]
        assert set(deployment["environment_owners"]["ATLAS_DATABASE_URL"]) == {
            "api",
            "migrate",
            "projector",
            "retrieval-indexer",
        }

    free_tier = (ROOT / "deploy" / "free-tier" / "compose.yaml").read_text(encoding="utf-8")
    assert free_tier.count("${ATLAS_POSTGRES_PASSWORD:?") == 5
    assert (ROOT / "compose.yaml").read_text(encoding="utf-8").count("gw_priority: 1") == 1
    assert free_tier.count("gw_priority: 1") == 1

    workflow = (WORKFLOW_DIRECTORY / "ci.yml").read_text(encoding="utf-8")
    assert "verify_compose_security.py --deployment base" in workflow
    assert "verify_compose_security.py --deployment free-tier" in workflow


def test_frontend_ci_smoke_tests_the_production_bundle() -> None:
    workflow = (WORKFLOW_DIRECTORY / "ci.yml").read_text(encoding="utf-8")
    build_step = "run: npm run build"
    browser_step = "run: npm run test:e2e"

    assert "npx playwright install --with-deps chromium" in workflow
    assert "run: npm run typecheck:e2e" in workflow
    assert build_step in workflow
    assert browser_step in workflow
    assert workflow.index(build_step) < workflow.index(browser_step)

    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["typecheck:e2e"].startswith("tsc --noEmit")
    assert package["scripts"]["test:e2e"] == "playwright test"

    config = (ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    assert 'command: "npm run preview ' in config
    assert "npm run dev" not in config


def test_browser_security_headers_are_synchronized_and_enforced() -> None:
    expected = json.loads((ROOT / "web" / "security-headers.json").read_text(encoding="utf-8"))
    policy = expected["Content-Security-Policy"]
    for directive in (
        "base-uri 'none'",
        "connect-src 'self' https://tiles.openfreemap.org",
        "font-src 'self' data: https://fonts.gstatic.com",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "worker-src 'self' blob:",
    ):
        assert directive in policy

    for relative_path in ("web/Caddyfile", "deploy/free-tier/Caddyfile"):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "-Server" in content
        for name, value in expected.items():
            assert f'{name} "{value}"' in content

    vite = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'import securityHeaders from "./security-headers.json"' in vite
    assert "headers: securityHeaders" in vite

    workflow = (WORKFLOW_DIRECTORY / "ci.yml").read_text(encoding="utf-8")
    assert "--dump-header /tmp/atlas-response-headers" in workflow
    assert 'Path("web/security-headers.json")' in workflow


def test_default_code_owner_is_explicit() -> None:
    rules = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    assert "* @Thivas12" in rules
