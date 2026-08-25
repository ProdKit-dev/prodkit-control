from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from prodkit_control_core import (
    EffectClass,
    PolicyOutcome,
    RiskClass,
    canonical_portable_json,
    sha256_hex,
)
from prodkit_control_runtime.policy_semantics import (
    ConstraintValue,
    PolicySemanticDecision,
    PolicySemanticResult,
    combine_policy_semantics,
    evaluate_default_policy_semantics,
)

ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE = ROOT / "contracts" / "conformance"


def _load(name: str) -> dict[str, Any]:
    return json.loads((CONFORMANCE / name).read_text(encoding="utf-8"))


def _result_payload(result: PolicySemanticResult) -> dict[str, object]:
    return {
        "outcome": result.outcome.value,
        "reason_codes": list(result.reason_codes),
        "required_approval_roles": list(result.required_approval_roles),
        "constraints": result.constraints,
    }


def check_canonicalization() -> None:
    document = _load("canonicalization-v1.json")
    if document.get("profile") != "prodkit-json-v1":
        raise ValueError("unexpected canonicalization conformance profile")
    for vector in document.get("vectors", []):
        if not isinstance(vector, dict):
            raise ValueError("canonicalization vector must be an object")
        vector_id = str(vector.get("id"))
        value = vector.get("input")
        encoded = canonical_portable_json(value)
        if encoded != vector.get("canonical_json"):
            raise ValueError(f"canonicalization mismatch for {vector_id}: {encoded!r}")
        digest = sha256_hex(encoded)
        if digest != vector.get("sha256"):
            raise ValueError(f"canonicalization digest mismatch for {vector_id}: {digest}")

    for vector in document.get("rejection_vectors", []):
        if not isinstance(vector, dict):
            raise ValueError("canonicalization rejection vector must be an object")
        vector_id = str(vector.get("id"))
        try:
            canonical_portable_json(vector.get("input"))
        except (TypeError, ValueError):
            continue
        raise ValueError(f"canonicalization unexpectedly accepted rejection vector {vector_id}")


def check_policy() -> None:
    document = _load("policy-v1.json")
    for vector in document.get("default_policy", []):
        if not isinstance(vector, dict):
            raise ValueError("default-policy vector must be an object")
        vector_id = str(vector.get("id"))
        input_value = cast(dict[str, object], vector.get("input"))
        result = evaluate_default_policy_semantics(
            effect_class=EffectClass(str(input_value.get("effect_class"))),
            risk_class=RiskClass(str(input_value.get("risk_class"))),
        )
        if _result_payload(result) != vector.get("expected"):
            raise ValueError(
                f"default policy mismatch for {vector_id}: {_result_payload(result)!r}"
            )

    for vector in document.get("conjunctive_policy", []):
        if not isinstance(vector, dict):
            raise ValueError("conjunctive-policy vector must be an object")
        vector_id = str(vector.get("id"))
        raw_decisions = cast(list[dict[str, object]], vector.get("decisions"))
        decisions = tuple(
            PolicySemanticDecision(
                engine=str(decision["engine"]),
                outcome=PolicyOutcome(str(decision["outcome"])),
                reason_codes=tuple(cast(list[str], decision.get("reason_codes", []))),
                required_approval_roles=tuple(
                    cast(list[str], decision.get("required_approval_roles", []))
                ),
                constraints=cast(
                    dict[str, ConstraintValue],
                    dict(cast(dict[str, object], decision.get("constraints", {}))),
                ),
            )
            for decision in raw_decisions
        )
        result = combine_policy_semantics(decisions)
        if _result_payload(result) != vector.get("expected"):
            raise ValueError(
                f"conjunctive policy mismatch for {vector_id}: {_result_payload(result)!r}"
            )


def main() -> None:
    check_canonicalization()
    check_policy()
    print("portable contract conformance: Python runtime passed shared vectors")


if __name__ == "__main__":
    main()
