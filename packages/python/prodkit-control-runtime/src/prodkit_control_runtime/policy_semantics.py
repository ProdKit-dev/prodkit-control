from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from prodkit_control_core import EffectClass, PolicyOutcome, RiskClass

ConstraintValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class PolicySemanticDecision:
    engine: str
    outcome: PolicyOutcome
    reason_codes: tuple[str, ...] = ()
    required_approval_roles: tuple[str, ...] = ()
    constraints: dict[str, ConstraintValue] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicySemanticResult:
    outcome: PolicyOutcome
    reason_codes: tuple[str, ...]
    required_approval_roles: tuple[str, ...]
    constraints: dict[str, ConstraintValue]


def evaluate_default_policy_semantics(
    *, effect_class: EffectClass, risk_class: RiskClass
) -> PolicySemanticResult:
    """Implement the language-neutral `prodkit-default-policy-v1` profile."""

    if effect_class is EffectClass.PRIVILEGED and risk_class is RiskClass.CRITICAL:
        return PolicySemanticResult(
            outcome=PolicyOutcome.DENY,
            reason_codes=("critical_privileged_action_denied_by_default",),
            required_approval_roles=(),
            constraints={},
        )
    if effect_class in {
        EffectClass.WRITE,
        EffectClass.DESTRUCTIVE,
        EffectClass.PRIVILEGED,
    } or risk_class in {RiskClass.HIGH, RiskClass.CRITICAL}:
        return PolicySemanticResult(
            outcome=PolicyOutcome.REQUIRE_APPROVAL,
            reason_codes=("side_effect_requires_exact_approval",),
            required_approval_roles=("production_approver",),
            constraints={},
        )
    return PolicySemanticResult(
        outcome=PolicyOutcome.ALLOW,
        reason_codes=("low_risk_action_allowed",),
        required_approval_roles=(),
        constraints={},
    )


def combine_policy_semantics(
    decisions: tuple[PolicySemanticDecision, ...],
) -> PolicySemanticResult:
    """Implement the language-neutral `prodkit-conjunctive-policy-v1` profile."""

    if not decisions:
        raise ValueError("conjunctive policy requires at least one decision")

    rank = {
        PolicyOutcome.ALLOW: 0,
        PolicyOutcome.REQUIRE_APPROVAL: 1,
        PolicyOutcome.DENY: 2,
    }
    outcome = max((decision.outcome for decision in decisions), key=rank.__getitem__)
    reasons = tuple(
        f"{decision.engine}:{reason}"
        for decision in decisions
        for reason in decision.reason_codes
    )
    roles = tuple(
        sorted({role for decision in decisions for role in decision.required_approval_roles})
    )

    constraints: dict[str, ConstraintValue] = {}
    conflicts: set[str] = set()
    for decision in decisions:
        for key, value in decision.constraints.items():
            if key in constraints and constraints[key] != value:
                conflicts.add(key)
            else:
                constraints[key] = value
    if conflicts:
        outcome = PolicyOutcome.DENY
        reasons += tuple(f"constraint_conflict:{key}" for key in sorted(conflicts))

    return PolicySemanticResult(
        outcome=outcome,
        reason_codes=reasons or ("all_policy_engines_returned_no_reason",),
        required_approval_roles=roles,
        constraints=constraints,
    )
