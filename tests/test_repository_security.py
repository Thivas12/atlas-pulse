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


def test_default_code_owner_is_explicit() -> None:
    rules = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    assert "* @Thivas12" in rules
