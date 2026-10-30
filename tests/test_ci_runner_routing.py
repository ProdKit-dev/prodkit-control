"""Check immutable PR runner isolation without altering the push runner policy."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("ci.yml", "security.yml", "codeql.yml")
PR_RUNNER = """github.event_name == 'pull_request' && '"ubuntu-latest"'"""
PUSH_RUNNER = """vars.PRODKIT_RUNNER_JSON != '' && vars.PRODKIT_RUNNER_JSON"""
SELF_HOSTED_DEFAULT = '["self-hosted","Linux","X64"]'


def test_pull_request_checks_use_ephemeral_hosted_runners() -> None:
    for filename in WORKFLOWS:
        content = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert PR_RUNNER in content, filename
        assert PUSH_RUNNER in content, filename
        assert SELF_HOSTED_DEFAULT in content, filename


def test_codeql_rejects_fork_pr_execution() -> None:
    content = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    assert "github.event.pull_request.head.repo.full_name == github.repository" in content
