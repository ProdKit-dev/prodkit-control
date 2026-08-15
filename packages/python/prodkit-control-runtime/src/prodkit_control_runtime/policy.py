from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from prodkit_control_core import (
    ActionSpec,
    EffectClass,
    PolicyDecision,
    PolicyOutcome,
    RiskClass,
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
