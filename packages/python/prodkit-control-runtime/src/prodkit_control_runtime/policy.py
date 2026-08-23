from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from prodkit_control_core import (
    ActionSpec,
    EffectClass,
    IntegrityViolationError,
    PolicyDecision,
    PolicyEngine,
    PolicyOutcome,
    RiskClass,
    sha256_hex,
)


class DefaultPolicyEngine:
    """Small deterministic reference policy.

    Production systems should replace or wrap this with an organization-owned policy bundle.
    """

    def __init__(self, *, revision: str = "default-policy-v1") -> None:
        self.revision = revision

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        if (
            action.effect_class is EffectClass.PRIVILEGED
            and action.risk_class is RiskClass.CRITICAL
        ):
            outcome = PolicyOutcome.DENY
            reasons = ("critical_privileged_action_denied_by_default",)
            roles: tuple[str, ...] = ()
        elif action.effect_class in {
            EffectClass.WRITE,
            EffectClass.DESTRUCTIVE,
            EffectClass.PRIVILEGED,
        } or action.risk_class in {RiskClass.HIGH, RiskClass.CRITICAL}:
            outcome = PolicyOutcome.REQUIRE_APPROVAL
            reasons = ("side_effect_requires_exact_approval",)
            roles = ("production_approver",)
        else:
            outcome = PolicyOutcome.ALLOW
            reasons = ("low_risk_action_allowed",)
            roles = ()
        now = datetime.now(UTC)
        return PolicyDecision(
            decision_id=uuid5(NAMESPACE_URL, f"prodkit-policy:{self.revision}:{action.digest}"),
            action_id=action.action_id,
            action_digest=action.digest,
            tenant_id=action.tenant_id,
            policy_engine="prodkit-default",
            policy_bundle="builtin",
            policy_revision=self.revision,
            evaluated_at=now,
            outcome=outcome,
            reason_codes=reasons,
            required_approval_roles=roles,
            expires_at=now + timedelta(minutes=15),
        )


class ConjunctivePolicyEngine:
    """Compose policy engines so no adapter can weaken a stricter decision.

    Every engine must produce a decision bound to the exact action and tenant. DENY dominates,
    REQUIRE_APPROVAL dominates ALLOW, approval roles are unioned, and incompatible constraints fail
    closed. This is the recommended compatibility boundary for OPA, Permit, AuthZen, and similar
    external policy decision points.
    """

    def __init__(self, engines: tuple[PolicyEngine, ...], *, revision: str = "conjunctive-v1") -> None:
        if not engines:
            raise ValueError("conjunctive policy requires at least one engine")
        self._engines = engines
        self._revision = revision

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        decisions = tuple([await engine.evaluate(action) for engine in self._engines])
        for decision in decisions:
            if decision.action_id != action.action_id or decision.action_digest != action.digest:
                raise IntegrityViolationError("policy adapter returned a decision for another action")
            if decision.tenant_id != action.tenant_id:
                raise IntegrityViolationError("policy adapter returned a cross-tenant decision")

        rank = {
            PolicyOutcome.ALLOW: 0,
            PolicyOutcome.REQUIRE_APPROVAL: 1,
            PolicyOutcome.DENY: 2,
        }
        outcome = max((decision.outcome for decision in decisions), key=rank.__getitem__)
        reasons = tuple(
            f"{decision.policy_engine}:{reason}"
            for decision in decisions
            for reason in decision.reason_codes
        )
        roles = tuple(
            sorted(
                {
                    role
                    for decision in decisions
                    for role in decision.required_approval_roles
                }
            )
        )

        constraints: dict[str, str | int | float | bool | None] = {}
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

        expiries = tuple(
            decision.expires_at for decision in decisions if decision.expires_at is not None
        )
        now = datetime.now(UTC)
        decision_fingerprint = sha256_hex(
            [
                {
                    "engine": decision.policy_engine,
                    "bundle": decision.policy_bundle,
                    "revision": decision.policy_revision,
                    "decision_id": str(decision.decision_id),
                    "outcome": decision.outcome.value,
                }
                for decision in decisions
            ]
        )
        return PolicyDecision(
            decision_id=uuid5(
                NAMESPACE_URL,
                f"prodkit-policy:{self._revision}:{action.digest}:{decision_fingerprint}",
            ),
            action_id=action.action_id,
            action_digest=action.digest,
            tenant_id=action.tenant_id,
            policy_engine="prodkit-conjunctive",
            policy_bundle="composed",
            policy_revision=self._revision,
            evaluated_at=now,
            outcome=outcome,
            reason_codes=reasons or ("all_policy_engines_returned_no_reason",),
            constraints=constraints,
            required_approval_roles=roles,
            expires_at=min(expiries) if expiries else None,
        )
