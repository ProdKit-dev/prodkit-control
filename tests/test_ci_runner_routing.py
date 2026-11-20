"""Ensure verification uses available runners without changing release trust."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("ci.yml", "security.yml", "codeql.yml")
HOSTED_RUNNER = """runner_json: '"ubuntu-latest"'"""
RELEASE_WORKFLOWS = (
    "release.yml",
    "release-verification.yml",
    "release-promotion.yml",
    "trusted-release-proof.yml",
)


def test_required_verification_uses_hosted_runners_on_all_events() -> None:
    for filename in WORKFLOWS:
        content = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert content.count(HOSTED_RUNNER) == 1, filename
        assert content.count("runner_json:") == 1, filename
        assert "vars.PRODKIT_RUNNER_JSON" not in content, filename
        assert '["self-hosted","Linux","X64"]' not in content, filename
        assert "\n  pull_request:\n" in content, filename
        assert "\n  push:\n" in content, filename


def test_scheduled_verification_also_uses_hosted_runners() -> None:
    for filename in ("security.yml", "codeql.yml"):
        content = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert "\n  schedule:\n" in content, filename
        assert HOSTED_RUNNER in content, filename


def test_release_authority_runner_remains_configurable() -> None:
    for filename in RELEASE_WORKFLOWS:
        content = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert "vars.PRODKIT_RUNNER_JSON" in content, filename
        assert '["self-hosted","Linux","X64"]' in content, filename


def test_codeql_rejects_fork_pr_execution() -> None:
    content = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    assert "github.event.pull_request.head.repo.full_name == github.repository" in content


def test_dependabot_npm_updates_root_pnpm_workspace() -> None:
    content = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert "  - package-ecosystem: npm\n    directory: /\n" in content
    assert "/packages/typescript/execution" not in content
    assert (ROOT / "package.json").is_file()
    assert (ROOT / "pnpm-workspace.yaml").is_file()
