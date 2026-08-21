from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx

from prodkit_control_core import (
    ActionSpec,
    CredentialLease,
    CredentialMaterialResolver,
    EffectClass,
    ExecutionResult,
    StateObservation,
    sha256_hex,
)


_BLOCKED_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization", "host"})


@dataclass(frozen=True)
class HTTPExecutorConfig:
    allowed_hosts: frozenset[str]
    allowed_methods: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    allowed_request_headers: frozenset[str] = frozenset({"content-type", "accept", "if-match"})
    timeout_seconds: float = 10.0
    max_request_bytes: int = 2 * 1024 * 1024
    max_response_bytes: int = 2 * 1024 * 1024
    require_https: bool = True
    require_observation_url: bool = True
    executor_identity: str = "spiffe://prodkit.local/executor/http"


class ConstrainedHTTPExecutor:
    """Host/method constrained HTTP executor with lease-local credential resolution."""

    name = "http"
    version = "1.0.0"

    def __init__(
        self,
        config: HTTPExecutorConfig,
        *,
        credential_resolver: CredentialMaterialResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not config.allowed_hosts:
            raise ValueError("HTTP executor requires at least one allowed host")
        self._config = config
        self._resolver = credential_resolver
        self._client = client
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        method = action.operation.upper()
        if method not in self._config.allowed_methods:
            raise PermissionError(f"HTTP method {method!r} is not allowed")
        url = self._url(action)
        self._validate_url(url)
        if action.target.resource_id != url:
            raise ValueError("HTTP target resource_id must equal arguments.url")
        headers = self._headers(action)
        if any(name.lower() in _BLOCKED_HEADERS for name in headers):
            raise PermissionError("caller-supplied credential/host headers are forbidden")
        if any(name.lower() not in self._config.allowed_request_headers for name in headers):
            raise PermissionError("HTTP request contains a non-allowlisted header")
        body = self._body(action)
        if len(body) > self._config.max_request_bytes:
            raise ValueError("HTTP request body exceeds configured maximum")
        if self._config.require_observation_url and action.effect_class is not EffectClass.READ:
            observe_url = action.arguments.get("observe_url")
            if not isinstance(observe_url, str) or not observe_url:
                raise ValueError("HTTP write requires arguments.observe_url")
            self._validate_url(observe_url)

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        return await self.execute_attempt(action, attempt_id=uuid4())

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        await self.validate(action)
        return await self._execute(action, attempt_id=attempt_id, credential_headers={})

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult:
        self._validate_lease(action, credential_lease)
        if self._resolver is None:
            raise PermissionError("HTTP executor has no worker-local credential resolver")
        material = await self._resolver.resolve(credential_lease)
        credential_headers: dict[str, str] = {}
        for key, value in material.items():
            if not key.lower().startswith("header:"):
                raise PermissionError("HTTP credential material may only provide header:* entries")
            name = key.split(":", 1)[1].strip()
            if not name or name.lower() == "host":
                raise PermissionError("invalid credential header name")
            credential_headers[name] = value
        return await self._execute(
            action,
            attempt_id=attempt_id,
            credential_headers=credential_headers,
        )

    async def _execute(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_headers: dict[str, str],
    ) -> ExecutionResult:
        started = datetime.now(UTC)
        headers = self._headers(action)
        headers.update(credential_headers)
        headers["Idempotency-Key"] = action.idempotency_key
        response = await self._request(
            action.operation.upper(),
            self._url(action),
            headers=headers,
            content=self._body(action),
        )
        content = response.content
        if len(content) > self._config.max_response_bytes:
            raise ValueError("HTTP response exceeds configured maximum")
        completed = datetime.now(UTC)
        succeeded = 200 <= response.status_code < 300
        provider_operation_id = response.headers.get("x-request-id") or response.headers.get(
            "request-id"
        )
        return ExecutionResult(
            action_id=action.action_id,
            execution_attempt_id=attempt_id,
            executor_name=self.name,
            executor_version=self.version,
            executor_identity=self.identity,
            started_at=started,
            completed_at=completed,
            succeeded=succeeded,
            provider_operation_id=provider_operation_id,
            result={
                "status_code": response.status_code,
                "body_sha256": sha256_hex(content),
                "body_size_bytes": len(content),
                "etag": response.headers.get("etag"),
            },
            retryable=(response.status_code == 429 or response.status_code >= 500),
            error_type=None if succeeded else "HTTPStatusError",
            error_message=None if succeeded else f"HTTP provider returned {response.status_code}",
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        observe_url = action.arguments.get("observe_url")
        if isinstance(observe_url, str) and observe_url:
            self._validate_url(observe_url)
            response = await self._request(
                "GET", observe_url, headers={"accept": "application/json"}
            )
            if len(response.content) > self._config.max_response_bytes:
                raise ValueError("HTTP observation response exceeds configured maximum")
            state: dict[str, object] = {
                "status_code": response.status_code,
                "body_sha256": sha256_hex(response.content),
                "body_size_bytes": len(response.content),
                "etag": response.headers.get("etag"),
            }
        else:
            state = {"execution_result": result.result}
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )

    def _url(self, action: ActionSpec) -> str:
        value = action.arguments.get("url")
        if not isinstance(value, str) or not value:
            raise ValueError("HTTP action requires arguments.url")
        return value

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        allowed_schemes = {"https"} if self._config.require_https else {"http", "https"}
        if (
            parsed.scheme not in allowed_schemes
            or parsed.hostname not in self._config.allowed_hosts
        ):
            raise PermissionError("HTTP URL is outside the configured scheme/host allowlist")
        if parsed.username or parsed.password or parsed.fragment:
            raise PermissionError("HTTP URL credentials/fragments are forbidden")

    def _headers(self, action: ActionSpec) -> dict[str, str]:
        raw = action.arguments.get("headers", {})
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
        ):
            raise ValueError("HTTP arguments.headers must be a string mapping")
        return {str(key): str(value) for key, value in raw.items()}

    @staticmethod
    def _body(action: ActionSpec) -> bytes:
        value = action.arguments.get("body")
        if value is None:
            return b""
        if isinstance(value, str):
            return value.encode()
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, content=content)
        async with httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
        ) as client:
            return await client.request(method, url, headers=headers, content=content)

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this HTTP action")
