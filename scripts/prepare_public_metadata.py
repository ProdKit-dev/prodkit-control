from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/ProdKit-dev/prodkit-control.git"
HOMEPAGE_URL = "https://github.com/ProdKit-dev/prodkit-control"
DOCUMENTATION_URL = f"{HOMEPAGE_URL}/tree/main/docs"
ISSUES_URL = f"{HOMEPAGE_URL}/issues"

_PYTHON_FIELD_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>readme|license-files|license)\s*=.*$"
)

_TS_DESCRIPTIONS = {
    "@prodkit/control": "Language-neutral TypeScript contracts and portable semantics for ProdKit Control.",
    "@prodkit/control-client": "HTTP client for the ProdKit Control API.",
    "@prodkit/control-next": "Next.js server client and guarded App Router route-handler integration for ProdKit Control.",
    "@prodkit/control-react": "React useSyncExternalStore-compatible data and mutation lifecycle primitives for ProdKit Control.",
}


def _project_section_bounds(lines: list[str], path: Path) -> tuple[int, int]:
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[project]":
            start = index
            continue
        if start is not None and stripped.startswith("[") and stripped.endswith("]"):
            return start, index
    if start is None:
        raise RuntimeError(f"{path.relative_to(ROOT)} has no [project] section")
    return start, len(lines)


def _normalize_python_project(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end = _project_section_bounds(lines, path)
    section = lines[start + 1 : end]

    desired = {
        "readme": 'readme = "README.md"',
        "license": 'license = "Apache-2.0"',
        "license-files": 'license-files = ["LICENSE", "NOTICE"]',
    }
    found: set[str] = set()
    for offset, line in enumerate(section, start=start + 1):
        match = _PYTHON_FIELD_RE.match(line)
        if match is None:
            continue
        key = match.group("key")
        lines[offset] = desired[key]
        found.add(key)

    insertion = end
    for key in ("readme", "license", "license-files"):
        if key not in found:
            lines.insert(insertion, desired[key])
            insertion += 1

    text = "\n".join(lines) + "\n"
    if "[project.urls]" not in text:
        text += (
            "\n[project.urls]\n"
            f'Homepage = "{HOMEPAGE_URL}"\n'
            f'Repository = "{REPOSITORY_URL}"\n'
            f'Documentation = "{DOCUMENTATION_URL}"\n'
            f'Issues = "{ISSUES_URL}"\n'
        )
    path.write_text(text, encoding="utf-8")


def _normalize_typescript_project(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    name = payload.get("name")
    if not isinstance(name, str):
        raise RuntimeError(f"{path.relative_to(ROOT)} has no package name")
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        try:
            payload["description"] = _TS_DESCRIPTIONS[name]
        except KeyError as exc:
            raise RuntimeError(
                f"{path.relative_to(ROOT)} has no description and no canonical description mapping"
            ) from exc

    payload["license"] = "Apache-2.0"
    payload["repository"] = {
        "type": "git",
        "url": REPOSITORY_URL,
        "directory": str(path.parent.relative_to(ROOT)).replace("\\", "/"),
    }
    payload["homepage"] = HOMEPAGE_URL
    payload["bugs"] = {"url": ISSUES_URL}
    payload["engines"] = {"node": ">=22"}
    payload["publishConfig"] = {"access": "public"}
    existing_files = payload.get("files")
    files = [item for item in existing_files if isinstance(item, str)] if isinstance(existing_files, list) else []
    for required in ("dist", "README.md", "LICENSE", "NOTICE"):
        if required not in files:
            files.append(required)
    payload["files"] = files
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize first-party package metadata for public distribution"
    )
    parser.parse_args()

    python_projects = [
        ROOT / "pyproject.toml",
        *sorted((ROOT / "packages/python").glob("**/pyproject.toml")),
    ]
    typescript_projects = sorted((ROOT / "packages/typescript").glob("*/package.json"))
    if not python_projects or not typescript_projects:
        raise RuntimeError("public package metadata surfaces were not discovered")

    for path in python_projects:
        _normalize_python_project(path)
    for path in typescript_projects:
        _normalize_typescript_project(path)

    print(
        f"normalized public metadata for {len(python_projects)} Python projects and "
        f"{len(typescript_projects)} TypeScript projects"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
