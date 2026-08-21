from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

import httpx

from prodkit_control_core import ActionSpec, CredentialLease, IntegrityViolationError


class HTTPCredentialLeaseProvider:
    """Short-lived workload credential lease broker.

    Only a non-secret credential reference is returned to the control plane. The isolated executor
    worker resolves that reference inside its own credential boundary, preventing callers/agents
    from obtaining production credentials directly.
    """

    def __init__(
        self,
        *,
        base_url: str,
        audience: str,
        scopes: tuple[str, ...] = (),
        bearer_token: str | None = None,
        max_ttl_seconds: int = 300,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        allowed_schemes = {"http", "https"} if allow_insecure_http else {"https"}
        if parsed.scheme not in allowed_schemes or not parsed.netloc:
            raise ValueError("credential lease provider requires an absolute HTTPS URL")
        if max_ttl_seconds < 30 or max_ttl_seconds > 3600:
            raise ValueError("credential lease TTL must be between 30 and 3600 seconds")
        self._base_url = base_url.rstrip("/")
        self._audience = audience
        self._scopes = scopes
        self._token = bearer_token
        self._ttl = max_ttl_seconds
        self._timeout = timeout_seconds
        self._client = client

    async def issue(self, *, action: ActionSpec, executor_identity: str) -> CredentialLease:
        response = await self._request(
            "POST",
            "/v1/credential-leases",
            json={
                "tenant_id": action.tenant_id,
                "action_id": str(action.action_id),
                "action_digest": action.digest,
                "executor_identity": executor_identity,
                "audience": self._audience,
                "scopes": list(self._scopes),
                "ttl_seconds": self._ttl,
            },
        )
        response.raise_for_status()
        try:
            lease = CredentialLease.model_validate(response.json())
        except Exception as exc:
            raise IntegrityViolationError("credential broker returned an invalid lease") from exc
        now = datetime.now(UTC)
        if lease.tenant_id != action.tenant_id or lease.action_id != action.action_id:
            raise IntegrityViolationError("credential lease is not bound to the current action")
        if lease.executor_identity != executor_identity:
            raise IntegrityViolationError("credential lease is not bound to the selected workload")
        if lease.audience != self._audience or tuple(lease.scopes) != self._scopes:
            raise IntegrityViolationError(
                "credential lease audience/scopes differ from requested scope"
            )
        if lease.issued_at > now + timedelta(seconds=30) or lease.expires_at <= now:
            raise IntegrityViolationError("credential lease is not currently valid")
        if lease.expires_at - lease.issued_at > timedelta(seconds=self._ttl):
            raise IntegrityViolationError("credential lease exceeds the configured maximum TTL")
        return lease

    async def revoke(self, lease_id: UUID) -> None:
        response = await self._request(
            "POST",
            f"/v1/credential-leases/{lease_id}:revoke",
            json={},
        )
        if response.status_code not in {204, 404}:
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
