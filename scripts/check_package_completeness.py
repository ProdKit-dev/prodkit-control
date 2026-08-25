from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / ".prodkit" / "package-completeness.json"
ALLOWED_STATUSES = {"supported", "optional_supported"}


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _python_manifests(root: Path) -> dict[str, Path]:
    return {
        path.parent.relative_to(root).as_posix(): path
        for path in sorted((root / "packages" / "python").glob("**/pyproject.toml"))
    }


def _typescript_manifests(root: Path) -> dict[str, Path]:
    return {
        path.parent.relative_to(root).as_posix(): path
        for path in sorted((root / "packages" / "typescript").glob("*/package.json"))
    }


def _python_has_implementation(package_dir: Path, manifest: dict[str, Any]) -> bool:
    uv = manifest.get("tool", {}).get("uv", {})
    build = uv.get("build-backend", {}) if isinstance(uv, dict) else {}
    module_name = build.get("module-name") if isinstance(build, dict) else None
    if not isinstance(module_name, str) or not module_name:
        raise ValueError(f"{package_dir}: missing tool.uv.build-backend.module-name")
    source_dir = package_dir / "src" / module_name
    if not source_dir.is_dir():
        raise ValueError(f"{package_dir}: missing source module {source_dir.relative_to(ROOT)}")
    parsed_any = False
    for path in sorted(source_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed_any = True
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                return True
    if not parsed_any:
        raise ValueError(f"{package_dir}: source module contains no Python files")
    return False


def _typescript_has_implementation(package_dir: Path) -> bool:
    source_dir = package_dir / "src"
    paths = sorted([*source_dir.rglob("*.ts"), *source_dir.rglob("*.tsx")])
    if not paths:
        raise ValueError(f"{package_dir}: src contains no TypeScript files")
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    without_blocks = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    semantic_lines: list[str] = []
    for raw_line in without_blocks.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if re.fullmatch(r"export const [A-Z0-9_]*VERSION\s*=.*", line):
            continue
        semantic_lines.append(line)
    if len(semantic_lines) < 5:
        return False
    semantic = "\n".join(semantic_lines)
    return bool(
        re.search(
            r"\b(?:class|function|interface|type|async|implements|extends)\b|=>",
            semantic,
        )
    )


def check(root: Path = ROOT) -> None:
    root_manifest = _load_toml(root / "pyproject.toml")
    project = root_manifest.get("project")
    root_version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(root_version, str) or not root_version:
        raise ValueError("root pyproject.toml is missing project.version")

    contract = json.loads((root / CONTRACT_PATH.relative_to(ROOT)).read_text(encoding="utf-8"))
    if contract.get("schema_version") != 1:
        raise ValueError("package completeness schema_version must be 1")
    if contract.get("release") != root_version:
        raise ValueError(
            f"package completeness release {contract.get('release')!r} != root {root_version!r}"
        )
    packages = contract.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise ValueError("package completeness contract requires a non-empty packages object")
    invalid_statuses = {
        str(path): status for path, status in packages.items() if status not in ALLOWED_STATUSES
    }
    if invalid_statuses:
        raise ValueError(f"unsupported package statuses: {invalid_statuses}")

    python = _python_manifests(root)
    typescript = _typescript_manifests(root)
    discovered = set(python) | set(typescript)
    declared = {str(path) for path in packages}
    if discovered != declared:
        missing = sorted(discovered - declared)
        stale = sorted(declared - discovered)
        raise ValueError(f"package completeness set mismatch: missing={missing}, stale={stale}")

    failures: list[str] = []
    for package_path, manifest_path in python.items():
        manifest = _load_toml(manifest_path)
        package_project = manifest.get("project")
        version = package_project.get("version") if isinstance(package_project, dict) else None
        if version != root_version:
            failures.append(f"{package_path}: version {version!r} != {root_version!r}")
        if not (manifest_path.parent / "README.md").is_file():
            failures.append(f"{package_path}: README.md is missing")
        try:
            if not _python_has_implementation(manifest_path.parent, manifest):
                failures.append(f"{package_path}: Python package is only scaffold/metadata")
        except (OSError, SyntaxError, ValueError) as exc:
            failures.append(str(exc))

    for package_path, manifest_path in typescript.items():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != root_version:
            failures.append(
                f"{package_path}: version {manifest.get('version')!r} != {root_version!r}"
            )
        if not _typescript_has_implementation(manifest_path.parent):
            failures.append(f"{package_path}: TypeScript package is only scaffold/metadata")

    if failures:
        raise ValueError("package completeness failed:\n- " + "\n- ".join(sorted(failures)))
    print(
        f"package completeness: {len(discovered)} first-party packages at {root_version}; "
        "no undeclared or scaffold packages"
    )


def main() -> None:
    check()


if __name__ == "__main__":
    main()
