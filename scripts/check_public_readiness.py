from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / ".prodkit/public-readiness.json"
REPOSITORY_URL = "https://github.com/ProdKit-dev/prodkit-control.git"
HOMEPAGE_URL = "https://github.com/ProdKit-dev/prodkit-control"

_REQUIRED_PUBLIC_FILES: dict[str, int] = {
    "README.md": 2_000,
    "LICENSE": 500,
    "NOTICE": 50,
    "SECURITY.md": 1_000,
    "SUPPORT.md": 500,
    "CONTRIBUTING.md": 2_000,
    "CODE_OF_CONDUCT.md": 1_000,
    "GOVERNANCE.md": 1_000,
    "VERIFICATION.md": 500,
    "docs/README.md": 2_000,
    "docs/getting-started.md": 2_000,
    "docs/releases/README.md": 1_000,
    "examples/README.md": 500,
    "examples/basic_dry_run.py": 1_000,
}

_REQUIRED_WORKFLOWS = {
    ".github/workflows/ci.yml",
    ".github/workflows/security.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/release.yml",
    ".github/workflows/release-verification.yml",
    ".github/workflows/branch-cleanup.yml",
}

_REQUIRED_PACKAGE_FILES = ("README.md", "LICENSE", "NOTICE")
_FORBIDDEN_SUPPORTED_DOC_MARKERS = (
    "This package is an optional adapter.",
    "Optional adapter boundary for external truth ingestion.",
    "Optional executor adapter package",
)


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return payload


def _root_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("pyproject.toml is missing project.version")
    return project["version"]


def _require_text(path: str, *, minimum_bytes: int) -> str:
    target = ROOT / path
    if not target.is_file():
        raise ValueError(f"required public file is missing: {path}")
    text = target.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) < minimum_bytes:
        raise ValueError(f"required public file is unexpectedly thin: {path}")
    return text


def _is_apache_license(value: object) -> bool:
    if value == "Apache-2.0":
        return True
    return isinstance(value, dict) and value.get("text") == "Apache-2.0"


def _check_package_public_files(package_dir: Path, *, status: str) -> None:
    root_license = (ROOT / "LICENSE").read_bytes()
    root_notice = (ROOT / "NOTICE").read_bytes()
    for name in _REQUIRED_PACKAGE_FILES:
        path = package_dir / name
        if not path.is_file():
            raise ValueError(f"{path.relative_to(ROOT)} is required for public distribution")
    if (package_dir / "LICENSE").read_bytes() != root_license:
        raise ValueError(f"{(package_dir / 'LICENSE').relative_to(ROOT)} drifted from root LICENSE")
    if (package_dir / "NOTICE").read_bytes() != root_notice:
        raise ValueError(f"{(package_dir / 'NOTICE').relative_to(ROOT)} drifted from root NOTICE")
    readme = (package_dir / "README.md").read_text(encoding="utf-8")
    if len(readme.encode("utf-8")) < 120:
        raise ValueError(f"{(package_dir / 'README.md').relative_to(ROOT)} is too thin for end users")
    if status == "supported":
        stale = [marker for marker in _FORBIDDEN_SUPPORTED_DOC_MARKERS if marker in readme]
        if stale:
            raise ValueError(
                f"{(package_dir / 'README.md').relative_to(ROOT)} contradicts supported maturity: {stale}"
            )


def _check_python_package_metadata(package_status: dict[str, str]) -> None:
    for path in sorted((ROOT / "packages/python").glob("**/pyproject.toml")):
        package_dir = path.parent
        package_key = str(package_dir.relative_to(ROOT)).replace("\\", "/")
        status = package_status.get(package_key)
        if status not in {"supported", "optional_supported"}:
            raise ValueError(f"{package_key} is missing from package-completeness authority")
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        project = payload.get("project")
        if not isinstance(project, dict):
            raise ValueError(f"{path.relative_to(ROOT)} is missing [project]")
        if project.get("readme") != "README.md":
            raise ValueError(f"{path.relative_to(ROOT)} must publish README.md metadata")
        if not _is_apache_license(project.get("license")):
            raise ValueError(f"{path.relative_to(ROOT)} must declare Apache-2.0")
        if project.get("license-files") != ["LICENSE", "NOTICE"]:
            raise ValueError(f"{path.relative_to(ROOT)} must ship LICENSE and NOTICE")
        urls = project.get("urls")
        if not isinstance(urls, dict):
            raise ValueError(f"{path.relative_to(ROOT)} must declare [project.urls]")
        if urls.get("Homepage") != HOMEPAGE_URL or urls.get("Repository") != REPOSITORY_URL:
            raise ValueError(f"{path.relative_to(ROOT)} has incorrect public project URLs")
        _check_package_public_files(package_dir, status=status)


def _check_typescript_package_metadata(package_status: dict[str, str]) -> None:
    for path in sorted((ROOT / "packages/typescript").glob("*/package.json")):
        package_dir = path.parent
        package_key = str(package_dir.relative_to(ROOT)).replace("\\", "/")
        status = package_status.get(package_key)
        if status not in {"supported", "optional_supported"}:
            raise ValueError(f"{package_key} is missing from package-completeness authority")
        payload = _load_json(path)
        if payload.get("license") != "Apache-2.0":
            raise ValueError(f"{path.relative_to(ROOT)} must declare Apache-2.0")
        description = payload.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{path.relative_to(ROOT)} must declare a package description")
        repository = payload.get("repository")
        if not isinstance(repository, dict) or repository.get("url") != REPOSITORY_URL:
            raise ValueError(f"{path.relative_to(ROOT)} must declare the canonical repository")
        if payload.get("homepage") != HOMEPAGE_URL:
            raise ValueError(f"{path.relative_to(ROOT)} must declare the canonical homepage")
        engines = payload.get("engines")
        if not isinstance(engines, dict) or engines.get("node") != ">=22":
            raise ValueError(f"{path.relative_to(ROOT)} must declare the supported Node floor")
        publish_config = payload.get("publishConfig")
        if not isinstance(publish_config, dict) or publish_config.get("access") != "public":
            raise ValueError(f"{path.relative_to(ROOT)} must be public-publication capable")
        files = payload.get("files")
        if not isinstance(files, list) or not {"dist", "README.md", "LICENSE", "NOTICE"}.issubset(files):
            raise ValueError(f"{path.relative_to(ROOT)} must publish dist, README, LICENSE, and NOTICE")
        _check_package_public_files(package_dir, status=status)
        if len((package_dir / "README.md").read_bytes()) < 500:
            raise ValueError(f"{(package_dir / 'README.md').relative_to(ROOT)} needs a usable package guide")


def main() -> int:
    contract = _load_json(CONTRACT)
    if contract.get("schema_version") != 1:
        raise ValueError("unsupported public-readiness schema_version")
    current_release = contract.get("current_release")
    next_milestone = contract.get("next_milestone")
    if not isinstance(current_release, str) or not isinstance(next_milestone, str):
        raise ValueError("public-readiness release/milestone values must be strings")

    root_version = _root_version()
    if root_version != current_release:
        raise ValueError(
            f"public-readiness release {current_release} != root project version {root_version}"
        )

    completeness = _load_json(ROOT / ".prodkit/package-completeness.json")
    if completeness.get("release") != current_release:
        raise ValueError("package-completeness release does not match public-readiness release")
    packages = completeness.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise ValueError("package-completeness authority has no packages")
    package_status = {
        key: value
        for key, value in packages.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    if len(package_status) != len(packages):
        raise ValueError("package-completeness package entries must be string:string")

    for path, minimum_bytes in _REQUIRED_PUBLIC_FILES.items():
        _require_text(path, minimum_bytes=minimum_bytes)
    for path in sorted(_REQUIRED_WORKFLOWS):
        if not (ROOT / path).is_file():
            raise ValueError(f"required lifecycle workflow is missing: {path}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required_readme_markers = {
        f"Current release: **v{current_release}**",
        f"Next milestone: **v{next_milestone}**",
        "uv run prodkit-control demo",
        "Language-neutral authority",
        "Security and support",
    }
    missing_markers = sorted(marker for marker in required_readme_markers if marker not in readme)
    if missing_markers:
        raise ValueError(f"README is missing current public markers: {missing_markers}")
    stale_markers = (
        "`v0.7.0` is the **reliability and disaster-recovery milestone**",
        "Through `v0.7.0`",
        "v0.8-v1.0 cover further",
    )
    present_stale = [marker for marker in stale_markers if marker in readme]
    if present_stale:
        raise ValueError(f"README still contains stale milestone claims: {present_stale}")

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if f"v{current_release}" not in security or "v0.8.0 supported profile" in security:
        raise ValueError("SECURITY.md does not identify the current supported release boundary")

    release_note = ROOT / f"docs/releases/v{current_release}.md"
    if not release_note.is_file():
        raise ValueError(f"missing release note: {release_note.relative_to(ROOT)}")

    _check_python_package_metadata(package_status)
    _check_typescript_package_metadata(package_status)

    print(
        f"Public readiness contract valid for v{current_release}; "
        f"next milestone v{next_milestone}; {len(package_status)} packages checked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
