#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CENTRAL_REPOSITORY = "ProdKit-dev/prodkit-workflows"
CENTRAL_SHA = "7f3d25ab467cfef1c1e2bcb397da461964f39204"

EXPECTED = {
    "ci.yml": "reusable-ci-compact.yml",
    "security.yml": "reusable-security-compact.yml",
    "codeql.yml": "reusable-codeql.yml",
    "trusted-release-proof.yml": "reusable-release-proof.yml",
    "release.yml": "reusable-release.yml",
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
                    f"{filename}: floating/non-canonical central reference: {referenced_workflow}@{ref}"
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

    proof = texts["trusted-release-proof.yml"]
    require(
        proof,
        "run-name: Trusted Release Proof — ${{ github.sha }}",
        workflow="trusted-release-proof.yml",
    )
    require(proof, "source_sha: ${{ github.sha }}", workflow="trusted-release-proof.yml")
    reject(proof, "inputs.source_sha", workflow="trusted-release-proof.yml")
    require(proof, 'python_version: "3.13"', workflow="trusted-release-proof.yml")
    require(proof, 'node_version: "24"', workflow="trusted-release-proof.yml")
    require(proof, 'pnpm_version: "10.15.0"', workflow="trusted-release-proof.yml")

    release = texts["release.yml"]
    require(
        release,
        "PROOF_WORKFLOW_FILE: .github/workflows/trusted-release-proof.yml",
        workflow="release.yml",
    )
    require(release, 'run.get("path") == workflow_file', workflow="release.yml")
    require(release, "target_sha: ${{ github.sha }}", workflow="release.yml")
    reject(release, "inputs.target_sha", workflow="release.yml")
    reject(release, "description: Exact current main SHA", workflow="release.yml")
    require(
        release, 'required_workflows_json: \'["CI","Security","CodeQL"]\'', workflow="release.yml"
    )
    require(release, "attest: false", workflow="release.yml")
    require(release, 'python_version: "3.13"', workflow="release.yml")
    require(release, 'node_version: "24"', workflow="release.yml")
    require(release, 'pnpm_version: "10.15.0"', workflow="release.yml")

    metadata = texts["release-metadata.yml"]
    require(
        metadata,
        "normalize_all_releases: ${{ github.event_name == 'push' }}",
        workflow="release-metadata.yml",
    )

    print(
        "workflow alignment contract satisfied: direct runners, compact CI/Security, "
        f"exact central pin {CENTRAL_SHA}, workflow-file release proof identity"
    )


if __name__ == "__main__":
    main()
