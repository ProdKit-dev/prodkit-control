from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
PROJECT_VERSION_RE = re.compile(r'^(version\s*=\s*)"[^"]+"(\s*)$')
PYTHON_VERSION_RE = re.compile(r'^(?P<prefix>__version__\s*=\s*)"[^"]+"(?P<suffix>\s*)$')
TS_RUNTIME_VERSION_RE = re.compile(
    r'^(?P<prefix>export const [A-Z0-9_]+_PACKAGE_VERSION\s*=\s*)"[^"]+"(?P<suffix>\s+as const;\s*)$'
)


def set_project_version(path: Path, version: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_project = False
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        match = PROJECT_VERSION_RE.match(line)
        if match is None:
            continue
        lines[index] = f'{match.group(1)}"{version}"{match.group(2)}'
        replaced = True
        break
    if not replaced:
        raise RuntimeError(f"{path.relative_to(ROOT)} has no [project] version")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_typescript_version(path: Path, version: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("version"), str):
        raise RuntimeError(f"{path.relative_to(ROOT)} has no package version")
    payload["version"] = version
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def set_python_runtime_versions(version: str) -> None:
    for path in sorted((ROOT / "packages/python").glob("**/__init__.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        changed = False
        for index, line in enumerate(lines):
            match = PYTHON_VERSION_RE.match(line)
            if match is None:
                continue
            lines[index] = f'{match.group("prefix")}"{version}"{match.group("suffix")}'
            changed = True
        if changed:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_typescript_runtime_versions(version: str) -> None:
    for path in sorted((ROOT / "packages/typescript").glob("*/src/**/*.ts")):
        lines = path.read_text(encoding="utf-8").splitlines()
        changed = False
        for index, line in enumerate(lines):
            match = TS_RUNTIME_VERSION_RE.match(line)
            if match is None:
                continue
            lines[index] = f'{match.group("prefix")}"{version}"{match.group("suffix")}'
            changed = True
        if changed:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_fastapi_metadata(version: str) -> None:
    path = ROOT / "packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py"
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(r'version="\d+\.\d+\.\d+"', f'version="{version}"', text, count=1)
    if count != 1:
        raise RuntimeError("FastAPI application metadata version was not found exactly once")
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set every first-party release version consistently"
    )
    parser.add_argument("version")
    args = parser.parse_args()
    version = args.version.strip()
    if VERSION_RE.fullmatch(version) is None:
        raise SystemExit(f"invalid semantic version: {version!r}")

    python_projects = [
        ROOT / "pyproject.toml",
        *sorted((ROOT / "packages/python").glob("**/pyproject.toml")),
    ]
    typescript_projects = sorted((ROOT / "packages/typescript").glob("**/package.json"))
    if not python_projects or not typescript_projects:
        raise RuntimeError("release-bearing package metadata was not found")

    for path in python_projects:
        set_project_version(path, version)
    for path in typescript_projects:
        set_typescript_version(path, version)
    set_typescript_runtime_versions(version)
    set_python_runtime_versions(version)
    set_fastapi_metadata(version)

    print(
        f"set release version {version} across {len(python_projects)} Python projects "
        f"and {len(typescript_projects)} TypeScript projects"
    )
    print("run `uv lock` next, then `python scripts/release_check.py --version` to verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
