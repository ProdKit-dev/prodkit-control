from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from prodkit_control_core import (
    ActionSpec,
    IntegrityViolationError,
    PolicyDecision,
    PolicyEngine,
    sha256_hex,
)

from .policy_semantics import (
    PolicySemanticDecision,
    combine_policy_semantics,
    evaluate_default_policy_semantics,
)


class DefaultPolicyEngine:
    """Reference adapter for the language-neutral `prodkit-default-policy-v1` profile.

    Production systems should replace or wrap this with an organization-owned policy bundle.
    """

    def __init__(self, *, revision: str = "default-policy-v1") -> None:
        self.revision = revision

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        semantic = evaluate_default_policy_semantics(
            effect_class=action.effect_class,
            risk_class=action.risk_class,
        )
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
            outcome=semantic.outcome,
            reason_codes=semantic.reason_codes,
            constraints=semantic.constraints,
            required_approval_roles=semantic.required_approval_roles,
            expires_at=now + timedelta(minutes=15),
        )


class ConjunctivePolicyEngine:
    """Compose adapters using the language-neutral fail-closed policy profile.

    Every engine must produce a decision bound to the exact action and tenant. The portable
    composition profile ensures no OPA, Permit, AuthZen, native, or future policy adapter can
    weaken a stricter decision.
    """

    def __init__(
        self, engines: tuple[PolicyEngine, ...], *, revision: str = "conjunctive-v1"
    ) -> None:
        if not engines:
            raise ValueError("conjunctive policy requires at least one engine")
        self._engines = engines
        self._revision = revision

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        collected: list[PolicyDecision] = []
        for engine in self._engines:
            collected.append(await engine.evaluate(action))
        decisions = tuple(collected)
        for decision in decisions:
            if decision.action_id != action.action_id or decision.action_digest != action.digest:
                raise IntegrityViolationError(
                    "policy adapter returned a decision for another action"
                )
            if decision.tenant_id != action.tenant_id:
                raise IntegrityViolationError("policy adapter returned a cross-tenant decision")

        semantic = combine_policy_semantics(
            tuple(
                PolicySemanticDecision(
                    engine=decision.policy_engine,
                    outcome=decision.outcome,
                    reason_codes=decision.reason_codes,
                    required_approval_roles=decision.required_approval_roles,
                    constraints=decision.constraints,
                )
                for decision in decisions
            )
        )

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
            outcome=semantic.outcome,
            reason_codes=semantic.reason_codes,
            constraints=semantic.constraints,
            required_approval_roles=semantic.required_approval_roles,
            expires_at=min(expiries) if expiries else None,
        )
