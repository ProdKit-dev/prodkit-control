from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from prodkit_control_core import ActionSpec, PolicyDecision, PolicyOutcome


class PermitCheck(Protocol):
    async def check(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        operation: str,
        resource_type: str,
        resource_id: str,
        context: Mapping[str, str | int | float | bool | None],
    ) -> Mapping[str, Any]: ...


class PermitPolicyEngine:
    """Map a Permit authorization/consent decision into the canonical policy contract."""

    def __init__(
        self,
        *,
        client: PermitCheck,
        bundle: str,
        revision: str,
        decision_ttl_seconds: int = 300,
    ) -> None:
        if not bundle or not revision:
            raise ValueError("Permit policy bundle and revision must be non-empty")
        if decision_ttl_seconds < 1:
            raise ValueError("Permit decision TTL must be positive")
        self._client = client
        self._bundle = bundle
        self._revision = revision
        self._ttl = decision_ttl_seconds

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        principal = action.policy_context.get("principal_id")
        if not isinstance(principal, str) or not principal:
            raise PermissionError("Permit evaluation requires policy_context.principal_id")
        raw = await self._client.check(
            tenant_id=action.tenant_id,
            principal_id=principal,
            operation=action.operation,
            resource_type=action.target.resource_type,
            resource_id=action.target.resource_id,
            context=action.policy_context,
        )
        approval_required = raw.get("approval_required") is True
        allowed = raw.get("allowed") is True
        outcome = (
            PolicyOutcome.REQUIRE_APPROVAL
            if approval_required
            else PolicyOutcome.ALLOW
            if allowed
            else PolicyOutcome.DENY
        )
        reason_codes_raw = raw.get("reason_codes", ())
        roles_raw = raw.get("required_approval_roles", ())
        constraints_raw = raw.get("constraints", {})
        if not isinstance(reason_codes_raw, (list, tuple)):
            reason_codes_raw = ("permit_invalid_reason_codes",)
            outcome = PolicyOutcome.DENY
        if not isinstance(roles_raw, (list, tuple)):
            roles_raw = ()
            outcome = PolicyOutcome.DENY
        if not isinstance(constraints_raw, Mapping):
            constraints_raw = {}
            outcome = PolicyOutcome.DENY
        constraints: dict[str, str | int | float | bool | None] = {}
        for key, value in constraints_raw.items():
            if isinstance(key, str) and (value is None or isinstance(value, (str, int, float, bool))):
                constraints[key] = value
        now = datetime.now(UTC)
        return PolicyDecision(
            decision_id=uuid5(NAMESPACE_URL, f"permit:{self._revision}:{action.digest}"),
            action_id=action.action_id,
            action_digest=action.digest,
            tenant_id=action.tenant_id,
            policy_engine="permit",
            policy_bundle=self._bundle,
            policy_revision=self._revision,
            evaluated_at=now,
            outcome=outcome,
            reason_codes=tuple(str(item) for item in reason_codes_raw if str(item)),
            constraints=constraints,
            required_approval_roles=tuple(str(item) for item in roles_raw if str(item)),
            expires_at=now + timedelta(seconds=self._ttl),
        )
