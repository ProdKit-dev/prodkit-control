from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "0.9.0"


def normalize_project_versions() -> None:
    pyprojects = [ROOT / "pyproject.toml", *sorted((ROOT / "packages/python").glob("**/pyproject.toml"))]
    for path in pyprojects:
        text = path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r'(?m)^version = "[^"]+"$',
            f'version = "{RELEASE}"',
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"could not normalize project version in {path.relative_to(ROOT)}")
        path.write_text(updated, encoding="utf-8")

    for path in sorted((ROOT / "packages/typescript").glob("*/package.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = RELEASE
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def normalize_runtime_versions() -> None:
    for path in sorted((ROOT / "packages/python").glob("**/*.py")):
        text = path.read_text(encoding="utf-8")
        updated = re.sub(
            r'(?m)^__version__ = "[^"]+"$',
            f'__version__ = "{RELEASE}"',
            text,
        )
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    app = ROOT / "packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py"
    text = app.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(?m)^        version="[^"]+",$',
        f'        version="{RELEASE}",',
        text,
        count=1,
    )
    if count != 1:
        raise ValueError("could not normalize FastAPI metadata version")
    app.write_text(updated, encoding="utf-8")


def update_changelog() -> None:
    changelog = ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    heading = "## [0.9.0] - 2026-08-25"
    if heading in text:
        return
    marker = "## [Unreleased]\n"
    if marker not in text:
        raise ValueError("CHANGELOG.md missing Unreleased marker")
    section = """

## [0.9.0] - 2026-08-25

### Added

- Production implementations for the database, Kubernetes, and deployment executor families that remained scaffolded in earlier milestones.
- Provider adapters for OpenAI, Anthropic, Google, and Pydantic AI; substantive E2B, Permit, Teleport, and Temporal integrations; and functional Next.js and React TypeScript surfaces.
- A language-neutral contract authority with normative specifications, canonicalization/policy profiles, shared conformance vectors, and independent Python/TypeScript implementations.
- Machine-discovered first-party package completeness plus direct v0.9 qualification tests for newly completed execution, sandbox, and provider boundaries.

### Changed

- All current first-party Python and TypeScript package manifests are version-aligned at 0.9.0 and frozen dependency metadata is regenerated from that exact workspace.
- Default and conjunctive policy semantics are defined by portable profiles rather than by Python or TypeScript implementation code; external policy engines remain optional adapters.
- The former Production Candidate milestone moves to v0.10.0; v0.9.0 is the cumulative-completeness and language-neutral-authority milestone.

### Assurance

- CI and release builds fail if package declarations drift from the discovered workspace, a package is scaffold-only, portable authority files disappear, cross-runtime conformance diverges, or frozen dependency state is stale.
- Release publication retains the inherited exact-source CI, Security, CodeQL, trusted release proof, immutable publication, and independent verification lifecycle.

### Release scope

v0.9.0 closes inherited first-party implementation and semantic-portability gaps. Optional integrations remain non-required runtime dependencies. This release does not claim the v0.10.0 Production Candidate or v1.0.0 Production Assurance profile.
"""
    changelog.write_text(text.replace(marker, marker + section, 1), encoding="utf-8")


def main() -> None:
    normalize_project_versions()
    normalize_runtime_versions()
    update_changelog()


if __name__ == "__main__":
    main()
