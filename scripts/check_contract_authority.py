from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "contracts" / "index.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _require_file(relative: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        raise ValueError(f"required contract-authority file is missing: {relative}")
    return path


def check() -> None:
    index = _load_json(INDEX_PATH)
    if index.get("schema_name") != "prodkit.control-contract-authority-index":
        raise ValueError("unexpected contract authority index schema")
    if index.get("schema_version") != "1.0.0":
        raise ValueError("unsupported contract authority index version")
    if index.get("authority") != "language-neutral":
        raise ValueError("portable contract authority must be language-neutral")

    roots = index.get("normative_roots")
    if not isinstance(roots, list) or not roots:
        raise ValueError("contract authority index requires normative_roots")
    forbidden_roots = {"packages/python", "packages/typescript"}
    for value in roots:
        relative = str(value)
        if relative in forbidden_roots or relative.startswith("packages/"):
            raise ValueError(f"runtime implementation cannot be a normative root: {relative}")
        if not (ROOT / relative).is_dir():
            raise ValueError(f"normative root does not exist: {relative}")

    for key in ("specifications", "conformance"):
        values = index.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"contract authority index requires non-empty {key}")
        for value in values:
            _require_file(str(value))

    runtimes = index.get("native_runtimes")
    if not isinstance(runtimes, dict) or set(runtimes) != {"python", "typescript"}:
        raise ValueError("v0.9 portable authority requires Python and TypeScript native runtimes")
    for runtime, metadata in runtimes.items():
        if not isinstance(metadata, dict) or metadata.get("role") != "implementation":
            raise ValueError(f"{runtime} must be declared as an implementation, not authority")
        root = ROOT / str(metadata.get("root", ""))
        if not root.is_dir():
            raise ValueError(f"native runtime root is missing for {runtime}: {root}")

    if "prodkit-json-v1" not in index.get("canonicalization_profiles", []):
        raise ValueError("prodkit-json-v1 must be indexed as a portable canonicalization profile")
    policy_profiles = set(index.get("policy_profiles", []))
    expected_policy_profiles = {
        "prodkit-default-policy-v1",
        "prodkit-conjunctive-policy-v1",
    }
    if policy_profiles != expected_policy_profiles:
        raise ValueError("v0.9 portable policy profile set is incomplete")

    _require_file(
        "packages/python/prodkit-control-runtime/src/"
        "prodkit_control_runtime/policy_semantics.py"
    )
    _require_file("packages/typescript/control/src/portable.ts")
    _require_file("scripts/check_contract_conformance.py")
    _require_file("scripts/check_contract_conformance.mjs")

    python_ci = _require_file(".prodkit/workflows/ci-python.sh").read_text(
        encoding="utf-8"
    )
    node_ci = _require_file(".prodkit/workflows/ci-node.sh").read_text(
        encoding="utf-8"
    )
    if "check_contract_authority.py" not in python_ci:
        raise ValueError("Python CI does not enforce contract authority")
    if "check_contract_conformance.py" not in python_ci:
        raise ValueError("Python CI does not enforce shared conformance vectors")
    if "check_contract_conformance.mjs" not in node_ci:
        raise ValueError("Node CI does not enforce shared conformance vectors")

    roadmap = _require_file("ROADMAP.md").read_text(encoding="utf-8")
    if "v0.9.0 — Cumulative completeness and language-neutral authority" not in roadmap:
        raise ValueError(
            "v0.9 roadmap does not gate cumulative completeness and language neutrality"
        )
    if "v0.10.0 — Production candidate" not in roadmap:
        raise ValueError("production candidate milestone has not been moved to v0.10.0")

    print(
        "contract authority: language-neutral specifications and cross-runtime gates are enforced"
    )


def main() -> None:
    check()


if __name__ == "__main__":
    main()
