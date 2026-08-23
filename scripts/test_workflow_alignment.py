#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CENTRAL_REPOSITORY = "ProdKit-dev/prodkit-workflows"
CENTRAL_SHA = "89eec1f6bcd4e45fb67c9fa99122ea6feba9d4bc"

EXPECTED = {
    "ci.yml": "reusable-ci-compact.yml",
    "security.yml": "reusable-security-compact.yml",
    "codeql.yml": "reusable-codeql.yml",
    "branch-cleanup.yml": "reusable-branch-cleanup.yml",
    "post-gate-branch-cleanup.yml": "reusable-gated-branch-cleanup.yml",
    "trusted-release-proof.yml": "reusable-release-proof.yml",
    "release-promotion.yml": "reusable-release-promote.yml",
    "release.yml": "reusable-release.yml",
    "release-verification.yml": "reusable-release-verification.yml",
    "release-metadata.yml": "reusable-release-metadata-current.yml",
}

CENTRAL_REF = re.compile(rf"{re.escape(CENTRAL_REPOSITORY)}/\.github/workflows/([^@\s]+)@([^\s]+)")

FORBIDDEN = (
    "reusable-runner-policy.yml@",
    "reusable-release-pipeline.yml@",
    "PRODKIT_RUNNER_MODE",
    "needs: runner",
    "needs.runner.outputs.runner_json",
    "options: [policy, auto, github-hosted, self-hosted]",
    "options: [self-hosted, github-hosted, auto, policy]",
)


def require(text: str, fragment: str, *, workflow: str) -> None:
    if fragment not in text:
        raise SystemExit(f"{workflow}: missing required workflow contract fragment: {fragment}")


def reject(text: str, fragment: str, *, workflow: str) -> None:
    if fragment in text:
        raise SystemExit(f"{workflow}: contains retired workflow contract fragment: {fragment}")


def main() -> None:
    texts: dict[str, str] = {}
    for filename, target in EXPECTED.items():
        path = WORKFLOW_DIR / filename
        if not path.is_file():
            raise SystemExit(f"missing lifecycle workflow: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        texts[filename] = text

        expected_ref = f"{CENTRAL_REPOSITORY}/.github/workflows/{target}@{CENTRAL_SHA}"
        require(text, expected_ref, workflow=filename)

        refs = CENTRAL_REF.findall(text)
        if not refs:
            raise SystemExit(f"{filename}: no central reusable-workflow reference found")
        for referenced_workflow, ref in refs:
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                raise SystemExit(
                    f"{filename}: floating/non-canonical central reference: "
                    f"{referenced_workflow}@{ref}"
                )
            if ref != CENTRAL_SHA:
                raise SystemExit(
                    f"{filename}: central SHA drift: {referenced_workflow}@{ref} != {CENTRAL_SHA}"
                )

        for fragment in FORBIDDEN:
            reject(text, fragment, workflow=filename)
        require(text, "runner_json:", workflow=filename)

    ci = texts["ci.yml"]
    require(ci, "reusable-ci-compact.yml@", workflow="ci.yml")
    require(ci, 'python_versions_json: \'["3.12","3.13","3.14"]\'', workflow="ci.yml")
    require(ci, 'node_versions_json: \'["22","24"]\'', workflow="ci.yml")
    require(ci, "postgres_enabled: true", workflow="ci.yml")

    security = texts["security.yml"]
    require(security, "reusable-security-compact.yml@", workflow="security.yml")
    require(security, "gitleaks_config_path: .gitleaks.toml", workflow="security.yml")
    require(security, "python_enabled: true", workflow="security.yml")
    require(security, "node_enabled: true", workflow="security.yml")

    codeql = texts["codeql.yml"]
    require(
        codeql,
        'languages_json: \'["python","javascript-typescript","actions"]\'',
        workflow="codeql.yml",
    )

    cleanup = texts["branch-cleanup.yml"]
    require(cleanup, "workflow_dispatch:", workflow="branch-cleanup.yml")
    require(cleanup, "branches_json:", workflow="branch-cleanup.yml")
    require(cleanup, "expected_default_sha:", workflow="branch-cleanup.yml")
    require(cleanup, "dry_run:", workflow="branch-cleanup.yml")
    require(cleanup, "default: true", workflow="branch-cleanup.yml")
    require(cleanup, "contents: write", workflow="branch-cleanup.yml")
    require(cleanup, "pull-requests: read", workflow="branch-cleanup.yml")
    expected_default_sha_contract = (
        "expected_default_sha: ${{ inputs.expected_default_sha != '' && "
        "inputs.expected_default_sha || github.sha }}"
    )
    require(cleanup, expected_default_sha_contract, workflow="branch-cleanup.yml")
    require(
        cleanup,
        "runner_json: ${{ vars.PRODKIT_RUNNER_JSON",
        workflow="branch-cleanup.yml",
    )
    require(
        cleanup,
        '["self-hosted","Linux","X64"]',
        workflow="branch-cleanup.yml",
    )
    reject(cleanup, "ubuntu-latest", workflow="branch-cleanup.yml")
    for forbidden_trigger in (
        "workflow_run:",
        "pull_request:",
        "push:",
        "issue_comment:",
        "schedule:",
        "pull_request_target:",
    ):
        reject(cleanup, forbidden_trigger, workflow="branch-cleanup.yml")

    post_gate_cleanup = texts["post-gate-branch-cleanup.yml"]
    require(post_gate_cleanup, "workflow_run:", workflow="post-gate-branch-cleanup.yml")
    require(
        post_gate_cleanup,
        'workflows: ["CI", "Security", "CodeQL"]',
        workflow="post-gate-branch-cleanup.yml",
    )
    require(post_gate_cleanup, "types: [completed]", workflow="post-gate-branch-cleanup.yml")
    require(post_gate_cleanup, "branches: [main]", workflow="post-gate-branch-cleanup.yml")
    require(
        post_gate_cleanup,
        "github.event.workflow_run.event == 'push'",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(
        post_gate_cleanup,
        "PRODKIT_GATED_CLEANUP_BRANCHES_JSON",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(
        post_gate_cleanup,
        "PRODKIT_GATED_CLEANUP_GATES_JSON",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(
        post_gate_cleanup,
        '"name":"CodeQL","path":".github/workflows/codeql.yml"',
        workflow="post-gate-branch-cleanup.yml",
    )
    require(
        post_gate_cleanup,
        "expected_default_sha: ${{ github.event.workflow_run.head_sha }}",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(
        post_gate_cleanup,
        "cleanup_workflow_file: branch-cleanup.yml",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(post_gate_cleanup, "actions: write", workflow="post-gate-branch-cleanup.yml")
    require(
        post_gate_cleanup,
        "runner_json: ${{ vars.PRODKIT_RUNNER_JSON",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(
        post_gate_cleanup,
        '["self-hosted","Linux","X64"]',
        workflow="post-gate-branch-cleanup.yml",
    )
    reject(post_gate_cleanup, "contents: write", workflow="post-gate-branch-cleanup.yml")

    proof = texts["trusted-release-proof.yml"]
    require(
        proof,
        "run-name: Trusted Release Proof — ${{ github.sha }}",
        workflow="trusted-release-proof.yml",
    )
    require(proof, "actions: read", workflow="trusted-release-proof.yml")
    require(proof, "source_sha: ${{ github.sha }}", workflow="trusted-release-proof.yml")
    reject(proof, "inputs.source_sha", workflow="trusted-release-proof.yml")
    require(
        proof,
        'required_workflows_json: \'["CI","Security","CodeQL"]\'',
        workflow="trusted-release-proof.yml",
    )
    require(proof, "manifest_path: .prodkit/release.json", workflow="trusted-release-proof.yml")
    require(proof, "prepare_release_payload: true", workflow="trusted-release-proof.yml")
    require(proof, 'python_version: "3.13"', workflow="trusted-release-proof.yml")
    require(proof, 'node_version: "24"', workflow="trusted-release-proof.yml")
    require(proof, 'pnpm_version: "10.15.0"', workflow="trusted-release-proof.yml")

    promotion = texts["release-promotion.yml"]
    require(promotion, "workflow_run:", workflow="release-promotion.yml")
    require(promotion, 'workflows: ["Trusted Release Proof"]', workflow="release-promotion.yml")
    require(promotion, "types: [completed]", workflow="release-promotion.yml")
    require(
        promotion,
        "github.event.workflow_run.conclusion == 'success'",
        workflow="release-promotion.yml",
    )
    require(
        promotion,
        "source_sha: ${{ github.event.workflow_run.head_sha }}",
        workflow="release-promotion.yml",
    )
    require(promotion, "release_workflow_file: release.yml", workflow="release-promotion.yml")
    reject(promotion, "while", workflow="release-promotion.yml")
    reject(promotion, "sleep", workflow="release-promotion.yml")

    release = texts["release.yml"]
    reject(release, "proof-gate:", workflow="release.yml")
    reject(release, "needs: proof-gate", workflow="release.yml")
    reject(release, "PROOF_WORKFLOW_FILE", workflow="release.yml")
    reject(release, "group: release-", workflow="release.yml")
    require(release, "target_sha: ${{ github.sha }}", workflow="release.yml")
    reject(release, "inputs.target_sha", workflow="release.yml")
    require(
        release,
        'required_workflows_json: \'["CI","Security","CodeQL"]\'',
        workflow="release.yml",
    )
    require(
        release,
        "proof_workflow_file: .github/workflows/trusted-release-proof.yml",
        workflow="release.yml",
    )
    require(release, "reuse_proof_payload: true", workflow="release.yml")
    require(release, "attest: false", workflow="release.yml")
    require(release, 'python_version: "3.13"', workflow="release.yml")
    require(release, 'node_version: "24"', workflow="release.yml")
    require(release, 'pnpm_version: "10.15.0"', workflow="release.yml")
    require(
        release,
        "reusable-release-verification-dispatch.yml@",
        workflow="release.yml",
    )
    for required in (
        "verification-dispatch:",
        "needs: release",
        "actions: write",
        "source_sha: ${{ github.sha }}",
        "version: ${{ inputs.version }}",
        "release_run_id: ${{ github.run_id }}",
        "verification_workflow_file: release-verification.yml",
        "runner_json: ${{ vars.PRODKIT_RUNNER_JSON",
        '["self-hosted","Linux","X64"]',
    ):
        require(release, required, workflow="release.yml")

    verification = texts["release-verification.yml"]
    require(
        verification,
        "run-name: Release Verification — ${{ github.sha }}",
        workflow="release-verification.yml",
    )
    require(verification, "workflow_dispatch:", workflow="release-verification.yml")
    require(verification, "release_run_id:", workflow="release-verification.yml")
    require(
        verification,
        "source_sha: ${{ github.sha }}",
        workflow="release-verification.yml",
    )
    require(
        verification,
        "release_run_id: ${{ inputs.release_run_id }}",
        workflow="release-verification.yml",
    )
    require(
        verification,
        "runner_json: ${{ vars.PRODKIT_RUNNER_JSON",
        workflow="release-verification.yml",
    )
    require(
        verification,
        '["self-hosted","Linux","X64"]',
        workflow="release-verification.yml",
    )
    require(verification, "manifest_path: .prodkit/release.json", workflow="release-verification.yml")
    require(verification, "release_workflow_file: release.yml", workflow="release-verification.yml")
    reject(verification, "workflow_run:", workflow="release-verification.yml")
    reject(verification, "actions: write", workflow="release-verification.yml")
    reject(verification, "contents: write", workflow="release-verification.yml")

    metadata = texts["release-metadata.yml"]
    require(
        metadata,
        "normalize_all_releases: ${{ github.event_name == 'push' }}",
        workflow="release-metadata.yml",
    )

    proof_adapter = (ROOT / ".prodkit/workflows/release-proof.sh").read_text(encoding="utf-8")
    for duplicated in (
        "pytest ",
        "ruff check",
        "ruff format",
        "mypy",
        "ci_postgres.py",
        "pip-audit",
        "pnpm audit",
        "pnpm typecheck",
        "pnpm build",
        "uv build",
        "scripts/inspect_release_artifacts.py",
    ):
        reject(proof_adapter, duplicated, workflow=".prodkit/workflows/release-proof.sh")
    require(
        proof_adapter,
        "Permanent exact-SHA CI, Security, and CodeQL are verified",
        workflow=".prodkit/workflows/release-proof.sh",
    )
    require(
        proof_adapter,
        "python3 scripts/release_check.py --version",
        workflow=".prodkit/workflows/release-proof.sh",
    )

    print(
        "workflow alignment contract satisfied: prodkit-workflows v0.1.3, "
        "exact-SHA branch cleanup, dormant post-gate cleanup authorization, direct compact gates, "
        "completed-proof promotion, proof-once payload reuse, release verification dispatch, "
        f"independent verification, exact central pin {CENTRAL_SHA}"
    )


if __name__ == "__main__":
    main()
