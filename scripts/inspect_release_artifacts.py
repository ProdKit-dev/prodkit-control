from __future__ import annotations

import argparse
import json
import re
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SUSPICIOUS_PARTS = {
    ".env",
    ".git",
    "__pycache__",
    "id_rsa",
    "node_modules",
}
_SUSPICIOUS_SUFFIXES = {".key", ".pem", ".pyc", ".pyo"}
_IGNORED_BUILD_MARKERS = {".gitignore"}
_POSTGRES_MIGRATION_MEMBERS = {
    "prodkit_control_postgres/migrations/__init__.py",
    "prodkit_control_postgres/migrations/0001_initial.sql",
    "prodkit_control_postgres/migrations/0002_hardened_execution.sql",
    "prodkit_control_postgres/migrations/0003_run_store_and_schema_metadata.sql",
}


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _metadata_identity(payload: bytes) -> tuple[str, str]:
    message = BytesParser(policy=default).parsebytes(payload)
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise ValueError("distribution metadata is missing Name or Version")
    return str(name), str(version)


def _reject_suspicious_members(artifact: Path, members: list[str]) -> None:
    for raw_name in members:
        path = Path(raw_name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{artifact.name}: unsafe archive member {raw_name!r}")
        lowered = {part.lower() for part in path.parts}
        if lowered.intersection(_SUSPICIOUS_PARTS):
            raise ValueError(f"{artifact.name}: forbidden archive member {raw_name!r}")
        if path.suffix.lower() in _SUSPICIOUS_SUFFIXES:
            raise ValueError(f"{artifact.name}: forbidden archive member {raw_name!r}")


def _wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        _reject_suspicious_members(path, names)
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        required_suffixes = (".dist-info/WHEEL", ".dist-info/RECORD")
        if len(metadata) != 1:
            raise ValueError(f"{path.name}: expected exactly one wheel METADATA file")
        for suffix in required_suffixes:
            if not any(name.endswith(suffix) for name in names):
                raise ValueError(f"{path.name}: wheel is missing {suffix.rsplit('/', 1)[-1]}")
        identity = _metadata_identity(archive.read(metadata[0]))
        if _canonical_name(identity[0]) == "prodkit-control-postgres":
            missing = sorted(_POSTGRES_MIGRATION_MEMBERS.difference(names))
            if missing:
                raise ValueError(
                    f"{path.name}: PostgreSQL wheel is missing packaged migrations {missing}"
                )
        return identity


def _sdist_identity(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        _reject_suspicious_members(path, names)
        metadata = [name for name in names if name.endswith("/PKG-INFO")]
        pyprojects = [name for name in names if name.endswith("/pyproject.toml")]
        if len(metadata) != 1:
            raise ValueError(f"{path.name}: expected exactly one sdist PKG-INFO file")
        if len(pyprojects) != 1:
            raise ValueError(f"{path.name}: expected exactly one sdist pyproject.toml")
        member = archive.extractfile(metadata[0])
        if member is None:
            raise ValueError(f"{path.name}: cannot read PKG-INFO")
        return _metadata_identity(member.read())


def _npm_identity(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        _reject_suspicious_members(path, names)
        package_json = [name for name in names if name == "package/package.json"]
        if len(package_json) != 1:
            raise ValueError(f"{path.name}: npm archive is missing package/package.json")
        member = archive.extractfile(package_json[0])
        if member is None:
            raise ValueError(f"{path.name}: cannot read package/package.json")
        payload = json.loads(member.read())
        name = payload.get("name")
        version = payload.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError(f"{path.name}: npm package is missing name/version")
        if not any(name.startswith("package/dist/") for name in names):
            raise ValueError(f"{path.name}: npm package does not contain built dist output")
        return name, version


def _expected_python() -> set[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "packages/python").glob("**/pyproject.toml")):
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        project = payload.get("project")
        if not isinstance(project, dict) or not isinstance(project.get("name"), str):
            raise ValueError(f"{path.relative_to(ROOT)} has no project.name")
        names.add(_canonical_name(project["name"]))
    return names


def _expected_typescript() -> set[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "packages/typescript").glob("*/package.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = payload.get("name")
        if not isinstance(name, str):
            raise ValueError(f"{path.relative_to(ROOT)} has no package name")
        names.add(name)
    return names


def inspect(directory: Path, expected_version: str) -> None:
    artifacts = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name not in _IGNORED_BUILD_MARKERS
    )
    if not artifacts:
        raise ValueError("release artifact directory is empty")

    python_kinds: dict[str, set[str]] = {}
    npm_packages: set[str] = set()
    for path in artifacts:
        if path.suffix == ".whl":
            name, version = _wheel_identity(path)
            kind = "wheel"
            canonical = _canonical_name(name)
            python_kinds.setdefault(canonical, set()).add(kind)
        elif path.name.endswith(".tar.gz"):
            name, version = _sdist_identity(path)
            kind = "sdist"
            canonical = _canonical_name(name)
            python_kinds.setdefault(canonical, set()).add(kind)
        elif path.suffix == ".tgz":
            name, version = _npm_identity(path)
            if name in npm_packages:
                raise ValueError(f"duplicate npm package artifact for {name}")
            npm_packages.add(name)
        else:
            raise ValueError(f"unexpected release artifact: {path.name}")

        if version != expected_version:
            raise ValueError(
                f"{path.name}: artifact version {version!r} does not match {expected_version!r}"
            )

    expected_python = _expected_python()
    actual_python = set(python_kinds)
    if actual_python != expected_python:
        missing = sorted(expected_python - actual_python)
        extra = sorted(actual_python - expected_python)
        raise ValueError(f"Python artifact set mismatch; missing={missing}, extra={extra}")
    for name, kinds in sorted(python_kinds.items()):
        if kinds != {"wheel", "sdist"}:
            raise ValueError(f"{name}: expected wheel+sdist, found {sorted(kinds)}")

    expected_npm = _expected_typescript()
    if npm_packages != expected_npm:
        missing = sorted(expected_npm - npm_packages)
        extra = sorted(npm_packages - expected_npm)
        raise ValueError(f"npm artifact set mismatch; missing={missing}, extra={extra}")

    print(
        "Verified "
        f"{len(expected_python)} Python packages (wheel+sdist) and "
        f"{len(expected_npm)} npm packages at {expected_version}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect ProdKit Control release artifacts")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    inspect(args.directory, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
