from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXACT_SHA = re.compile(r"^[a-f0-9]{40}$")


def _local_dependency_paths(target: str, violations: list[str]) -> tuple[Path, ...]:
    root = ROOT.resolve()
    resolved = (root / target[2:]).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        violations.append(f"local action path escapes repository: {target}")
        return ()

    if resolved.is_file():
        return (resolved,)
    if resolved.is_dir():
        metadata = tuple(
            path for path in (resolved / "action.yml", resolved / "action.yaml") if path.is_file()
        )
        if metadata:
            return metadata
    violations.append(f"local action metadata is missing: {target}")
    return ()


def _iter_uses(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            yield from _iter_uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_uses(item)


def _scan_uses(path: Path, violations: list[str], visited: set[Path]) -> None:
    resolved_path = path.resolve()
    if resolved_path in visited:
        return
    visited.add(resolved_path)

    relative_path = resolved_path.relative_to(ROOT.resolve())
    try:
        document = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        violations.append(f"{relative_path}: invalid YAML: {exc}")
        return

    for target in _iter_uses(document):
        target = target.strip()
        if target.startswith("./"):
            for dependency in _local_dependency_paths(target, violations):
                _scan_uses(dependency, violations, visited)
            continue
        if "@" not in target:
            violations.append(f"{relative_path}: unpinned action {target}")
            continue
        _, ref = target.rsplit("@", 1)
        if not EXACT_SHA.fullmatch(ref):
            violations.append(
                f"{relative_path}: action/reusable workflow ref is not an exact SHA: {target}"
            )


def check_workflow_pins() -> None:
    violations: list[str] = []
    visited: set[Path] = set()
    workflow_paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    for path in workflow_paths:
        _scan_uses(path, violations, visited)
    if violations:
        raise SystemExit("\n".join(violations))


def check_release_contract() -> None:
    manifest_path = ROOT / ".prodkit" / "release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = manifest.get("build")
    if not isinstance(build, dict) or build.get("source_archive") is not True:
        raise SystemExit("release manifest must require a source archive")
    script = build.get("script")
    if not isinstance(script, str) or not script.startswith(".prodkit/workflows/"):
        raise SystemExit("release build script must be repository-controlled")
    for lockfile in (ROOT / "uv.lock", ROOT / "pnpm-lock.yaml"):
        if not lockfile.is_file():
            raise SystemExit(f"required dependency lockfile is missing: {lockfile.name}")


def check_kubernetes_baseline() -> None:
    deployment = (ROOT / "deploy" / "kubernetes" / "base" / "deployment.yaml").read_text(
        encoding="utf-8"
    )
    required = (
        "runAsNonRoot: true",
        "seccompProfile:",
        "type: RuntimeDefault",
        "allowPrivilegeEscalation: false",
        "readOnlyRootFilesystem: true",
        'capabilities: { drop: ["ALL"] }',
        "automountServiceAccountToken: false",
    )
    missing = [fragment for fragment in required if fragment not in deployment]
    if missing:
        raise SystemExit(f"Kubernetes hardening baseline is incomplete: {missing}")
    if ":latest" in deployment:
        raise SystemExit("Kubernetes base must not deploy the mutable :latest image tag")


def main() -> None:
    check_workflow_pins()
    check_release_contract()
    check_kubernetes_baseline()
    print("security policy: workflow pins, release locks, and Kubernetes baseline verified")


if __name__ == "__main__":
    main()
