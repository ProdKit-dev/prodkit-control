from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")
TEMPORARY_RELEASE_PATHS = (ROOT / ".github/workflows/v0.0.0-normalize.yml",)


def _version(value: str) -> str:
    match = VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"expected a semantic version or v-prefixed tag, got {value!r}")
    return match.group(1)


def _toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _python_packages() -> dict[str, tuple[str, Path]]:
    result: dict[str, tuple[str, Path]] = {}
    for path in [
        ROOT / "pyproject.toml",
        *sorted((ROOT / "packages/python").glob("**/pyproject.toml")),
    ]:
        project = _toml(path).get("project")
        if not isinstance(project, dict):
            continue
        name = project.get("name")
        version = project.get("version")
        if isinstance(name, str) and isinstance(version, str):
            result[name] = (version, path)
    return result


def _typescript_packages() -> dict[str, tuple[str, Path]]:
    result: dict[str, tuple[str, Path]] = {}
    for path in sorted((ROOT / "packages/typescript").glob("**/package.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = payload.get("name")
        version = payload.get("version")
        if isinstance(name, str) and isinstance(version, str):
            result[name] = (version, path)
    return result


def _locked_versions() -> dict[str, set[str]]:
    payload = _toml(ROOT / "uv.lock")
    packages = payload.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock does not contain a package array")
    result: dict[str, set[str]] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        version = package.get("version")
        if isinstance(name, str) and isinstance(version, str):
            result.setdefault(name, set()).add(version)
    return result


def verify(expected: str) -> list[str]:
    failures: list[str] = []
    python_packages = _python_packages()
    for name, (version, path) in sorted(python_packages.items()):
        if version != expected:
            failures.append(f"{path.relative_to(ROOT)}: {name} is {version}, expected {expected}")

    for name, (version, path) in sorted(_typescript_packages().items()):
        if version != expected:
            failures.append(f"{path.relative_to(ROOT)}: {name} is {version}, expected {expected}")

    locked = _locked_versions()
    for name in sorted(python_packages):
        versions = locked.get(name, set())
        if expected not in versions:
            rendered = ", ".join(sorted(versions)) or "missing"
            failures.append(f"uv.lock: {name} has {rendered}, expected {expected}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{expected}] - " not in changelog:
        failures.append(f"CHANGELOG.md has no released {expected} section")

    app_text = (
        ROOT / "packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py"
    ).read_text(encoding="utf-8")
    if f'version="{expected}"' not in app_text:
        failures.append(f"FastAPI metadata does not expose version {expected}")

    for path in TEMPORARY_RELEASE_PATHS:
        if path.exists():
            failures.append(f"temporary release machinery still exists: {path.relative_to(ROOT)}")

    if (ROOT / "REPOSITORY_MANIFEST.sha256").exists():
        failures.append("stale REPOSITORY_MANIFEST.sha256 must not ship")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a ProdKit Control release source tree")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--version")
    group.add_argument("--tag")
    args = parser.parse_args()
    expected = _version(args.version or args.tag)
    failures = verify(expected)
    if failures:
        print("Release contract failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Release contract verified for {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
