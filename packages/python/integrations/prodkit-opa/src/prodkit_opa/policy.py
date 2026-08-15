from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import httpx

from prodkit_control_core import ActionSpec, PolicyDecision, PolicyOutcome


class OPAHttpPolicyEngine:
    """Evaluate canonical actions against an OPA data API endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        policy_path: str,
        bundle: str,
        revision: str,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/data/{policy_path.strip('/')}"
        self._bundle = bundle
        self._revision = revision
        self._timeout = timeout_seconds
        self._client = client

    async def evaluate(self, action: ActionSpec) -> PolicyDecision:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                self._url,
                json={
                    "input": {
                        "action": action.model_dump(mode="json"),
                        "action_digest": action.digest,
                    }
                },
            )
            response.raise_for_status()
            result = response.json().get("result")
        finally:
            if owns_client:
                await client.aclose()
        if not isinstance(result, dict):
            outcome = PolicyOutcome.DENY
            reasons = ("opa_missing_structured_result",)
            roles: tuple[str, ...] = ()
            constraints = {}
        else:
            try:
                outcome = PolicyOutcome(str(result.get("outcome", "deny")))
            except ValueError:
                outcome = PolicyOutcome.DENY
            reasons = tuple(str(item) for item in result.get("reason_codes", ()))
            roles = tuple(str(item) for item in result.get("required_approval_roles", ()))
            constraints = dict(result.get("constraints", {}))
        now = datetime.now(UTC)
        return PolicyDecision(
            decision_id=uuid5(NAMESPACE_URL, f"opa:{self._revision}:{action.digest}"),
            action_id=action.action_id,
            action_digest=action.digest,
            tenant_id=action.tenant_id,
            policy_engine="opa",
            policy_bundle=self._bundle,
            policy_revision=self._revision,
            evaluated_at=now,
            outcome=outcome,
            reason_codes=reasons,
            constraints=constraints,
            required_approval_roles=roles,
            expires_at=now + timedelta(minutes=15),
        )
