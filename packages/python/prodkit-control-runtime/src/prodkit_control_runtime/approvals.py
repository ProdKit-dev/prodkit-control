from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from prodkit_control_core import (
    ActionSpec,
    ApprovalDecision,
    IntegrityViolationError,
    PolicyDecision,
)


class HTTPApprovalProvider:
    """Digest-bound approval provider for an external approval service.

    The remote service never receives executor credentials. A returned approval is accepted only
    when its action, target, tenant, environment, policy decision/revision, and expiry all match
    the currently evaluated action.
    """

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in ({"https"} if not allow_insecure_http else {"http", "https"}):
            raise ValueError("approval provider requires HTTPS")
        if not parsed.netloc:
            raise ValueError("approval provider requires an absolute base URL")
        self._base_url = base_url.rstrip("/")
        self._token = bearer_token
        self._timeout = timeout_seconds
        self._client = client

    async def find_valid_approval(
        self,
        *,
        action: ActionSpec,
        policy_decision: PolicyDecision,
        target_digest: str,
    ) -> ApprovalDecision | None:
        response = await self._request(
            "POST",
            "/v1/approvals:resolve",
            json={
                "tenant_id": action.tenant_id,
                "action_id": str(action.action_id),
                "action_digest": action.digest,
                "target_digest": target_digest,
                "environment": action.target.environment,
                "policy_decision_id": str(policy_decision.decision_id),
                "policy_revision": policy_decision.policy_revision,
                "required_roles": list(policy_decision.required_approval_roles),
            },
        )
        if response.status_code in {204, 404}:
            return None
        response.raise_for_status()
        try:
            approval = ApprovalDecision.model_validate(response.json())
        except Exception as exc:
            raise IntegrityViolationError("approval provider returned an invalid decision") from exc
        if not approval.authorizes(
            action_digest=action.digest,
            target_digest=target_digest,
            policy_decision_id=policy_decision.decision_id,
            policy_revision=policy_decision.policy_revision,
            tenant_id=action.tenant_id,
            environment=action.target.environment,
            at=datetime.now(UTC),
        ):
            raise IntegrityViolationError(
                "approval provider returned a decision with stale bindings"
            )
        return approval

    async def record(self, decision: ApprovalDecision) -> None:
        response = await self._request(
            "POST",
            "/v1/approvals",
            json=decision.model_dump(mode="json"),
        )
        response.raise_for_status()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object],
    ) -> httpx.Response:
        headers = {"accept": "application/json"}
        if self._token is not None:
            headers["authorization"] = f"Bearer {self._token}"
        if self._client is not None:
            return await self._client.request(
                method, f"{self._base_url}{path}", json=json, headers=headers
            )
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            return await client.request(
                method, f"{self._base_url}{path}", json=json, headers=headers
            )
