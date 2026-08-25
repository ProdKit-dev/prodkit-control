#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CENTRAL_REPOSITORY = "ProdKit-dev/prodkit-workflows"
CENTRAL_SHA = "e77861d685e0aaaabd43a385c9f26297d3598792"
REQUIRED_GATES = '["CI","Security","CodeQL"]'
TRUSTED_RUNNER = (
    "runner_json: ${{ vars.PRODKIT_RUNNER_JSON != '' && vars.PRODKIT_RUNNER_JSON || "
    '\'["self-hosted","Linux","X64"]\' }}'
)
HOSTED_CONTROL = "vars.PRODKIT_GITHUB_HOSTED_CONTROL_PLANE == 'true'"

EXPECTED = {
    "ci.yml": "reusable-ci-compact.yml",
    "security.yml": "reusable-security-compact.yml",
    "codeql.yml": "reusable-codeql.yml",
    "branch-cleanup.yml": "reusable-branch-cleanup.yml",
    "post-gate-branch-cleanup.yml": "reusable-gated-branch-cleanup.yml",
    "release-proof-dispatch.yml": "reusable-release-proof-dispatch.yml",
    "trusted-release-proof.yml": "reusable-release-proof.yml",
    "release-promotion.yml": "reusable-release-promote.yml",
    "release.yml": "reusable-release.yml",
    "release-verification.yml": "reusable-release-verification.yml",
    "release-metadata.yml": "reusable-release-metadata-current.yml",
}

CENTRAL_REF = re.compile(rf"{re.escape(CENTRAL_REPOSITORY)}/\.github/workflows/([^@\s]+)@([^\s]+)")


def require(text: str, fragment: str, *, workflow: str) -> None:
    if fragment not in text:
        raise SystemExit(f"{workflow}: missing required lifecycle fragment: {fragment}")


def reject(text: str, fragment: str, *, workflow: str) -> None:
    if fragment in text:
        raise SystemExit(f"{workflow}: contains forbidden lifecycle fragment: {fragment}")


def main() -> None:
    texts: dict[str, str] = {}
    for filename, reusable in EXPECTED.items():
        path = WORKFLOW_DIR / filename
        if not path.is_file():
            raise SystemExit(f"missing lifecycle workflow: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        texts[filename] = text
        require(
            text,
            f"{CENTRAL_REPOSITORY}/.github/workflows/{reusable}@{CENTRAL_SHA}",
            workflow=filename,
        )
        refs = CENTRAL_REF.findall(text)
        if not refs:
            raise SystemExit(f"{filename}: no central reusable-workflow reference found")
        for referenced, ref in refs:
            if ref != CENTRAL_SHA:
                raise SystemExit(
                    f"{filename}: central pin drift: {referenced}@{ref} != {CENTRAL_SHA}"
                )
        reject(text, "@main", workflow=filename)
        reject(text, "@v0.", workflow=filename)

    ci = texts["ci.yml"]
    require(ci, 'python_versions_json: \'["3.12","3.13","3.14"]\'', workflow="ci.yml")
    require(ci, 'node_versions_json: \'["22","24"]\'', workflow="ci.yml")
    require(ci, "postgres_enabled: true", workflow="ci.yml")

    security = texts["security.yml"]
    require(security, "gitleaks_config_path: .gitleaks.toml", workflow="security.yml")
    require(security, "python_enabled: true", workflow="security.yml")
    require(security, "node_enabled: true", workflow="security.yml")

    codeql = texts["codeql.yml"]
    require(
        codeql,
        'languages_json: \'["python","javascript-typescript","actions"]\'',
        workflow="codeql.yml",
    )

    dispatch = texts["release-proof-dispatch.yml"]
    require(
        dispatch, 'workflows: ["CI", "Security", "CodeQL"]', workflow="release-proof-dispatch.yml"
    )
    require(
        dispatch,
        f"required_workflows_json: '{REQUIRED_GATES}'",
        workflow="release-proof-dispatch.yml",
    )
    require(
        dispatch,
        "reusable-release-proof-promotion-dispatch.yml@",
        workflow="release-proof-dispatch.yml",
    )
    release_intent = "startsWith(github.event.workflow_run.head_commit.message, 'release: v')"
    if dispatch.count(release_intent) != 2:
        raise SystemExit(
            "release-proof-dispatch.yml: both automatic jobs must require release intent"
        )
    require(dispatch, TRUSTED_RUNNER, workflow="release-proof-dispatch.yml")
    require(dispatch, HOSTED_CONTROL, workflow="release-proof-dispatch.yml")
    require(dispatch, "runner_json: '\"ubuntu-latest\"'", workflow="release-proof-dispatch.yml")
    require(dispatch, "actions: write", workflow="release-proof-dispatch.yml")
    reject(dispatch, "contents: write", workflow="release-proof-dispatch.yml")

    proof = texts["trusted-release-proof.yml"]
    require(
        proof, f"required_workflows_json: '{REQUIRED_GATES}'", workflow="trusted-release-proof.yml"
    )
    require(proof, "source_sha: ${{ github.sha }}", workflow="trusted-release-proof.yml")
    require(proof, "prepare_release_payload: true", workflow="trusted-release-proof.yml")
    require(proof, "promote proven release", workflow="trusted-release-proof.yml")
    require(proof, "needs: proof", workflow="trusted-release-proof.yml")
    require(
        proof, "PRODKIT_GITHUB_HOSTED_CONTROL_PLANE != 'true'", workflow="trusted-release-proof.yml"
    )
    require(proof, "reusable-release-promote.yml@", workflow="trusted-release-proof.yml")
    require(proof, TRUSTED_RUNNER, workflow="trusted-release-proof.yml")

    promotion = texts["release-promotion.yml"]
    require(promotion, 'workflows: ["Trusted Release Proof"]', workflow="release-promotion.yml")
    require(promotion, "workflow_dispatch:", workflow="release-promotion.yml")
    require(promotion, "proof_run_id:", workflow="release-promotion.yml")
    require(promotion, HOSTED_CONTROL, workflow="release-promotion.yml")
    require(promotion, TRUSTED_RUNNER, workflow="release-promotion.yml")
    for forbidden in ("time.sleep(", "while time.time()", "wait_for_release"):
        reject(promotion, forbidden, workflow="release-promotion.yml")

    release = texts["release.yml"]
    require(release, f"required_workflows_json: '{REQUIRED_GATES}'", workflow="release.yml")
    require(release, "reuse_proof_payload: true", workflow="release.yml")
    require(release, "reusable-release-verification-dispatch.yml@", workflow="release.yml")
    require(release, "release_run_id: ${{ github.run_id }}", workflow="release.yml")

    verification = texts["release-verification.yml"]
    for fragment in (
        "actions: write",
        "contents: read",
        "pull-requests: read",
        "automatic_cleanup: true",
        "cleanup_workflow_file: branch-cleanup.yml",
        "main_branch: main",
        'cleanup_branch_prefixes_json: \'["release/","hotfix/"]\'',
    ):
        require(verification, fragment, workflow="release-verification.yml")
    reject(verification, "contents: write", workflow="release-verification.yml")
    reject(verification, "workflow_run:", workflow="release-verification.yml")

    cleanup = texts["branch-cleanup.yml"]
    require(cleanup, "workflow_dispatch:", workflow="branch-cleanup.yml")
    require(cleanup, "expected_default_sha:", workflow="branch-cleanup.yml")
    require(cleanup, "contents: write", workflow="branch-cleanup.yml")
    require(cleanup, "pull-requests: read", workflow="branch-cleanup.yml")
    require(cleanup, TRUSTED_RUNNER, workflow="branch-cleanup.yml")

    post_gate = texts["post-gate-branch-cleanup.yml"]
    require(
        post_gate,
        f"required_gates_json: ${{{{ vars.PRODKIT_GATED_CLEANUP_GATES_JSON != ''",
        workflow="post-gate-branch-cleanup.yml",
    )
    require(post_gate, TRUSTED_RUNNER, workflow="post-gate-branch-cleanup.yml")
    reject(post_gate, "contents: write", workflow="post-gate-branch-cleanup.yml")

    adapter = (ROOT / ".prodkit/workflows/release-proof.sh").read_text(encoding="utf-8")
    require(
        adapter,
        "Permanent exact-SHA CI, Security, and CodeQL are verified",
        workflow=".prodkit/workflows/release-proof.sh",
    )

    print(
        "workflow alignment contract satisfied: immutable prodkit-workflows v0.1.6, "
        "trusted private control plane, serialized proof promotion, explicit release intent, "
        "publication verification, and verified cleanup"
    )


if __name__ == "__main__":
    main()
