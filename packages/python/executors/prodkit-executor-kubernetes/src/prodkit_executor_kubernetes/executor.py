from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4

import httpx

from prodkit_control_core import (
    ActionSpec,
    CredentialLease,
    CredentialMaterialResolver,
    ExecutionResult,
    StateObservation,
    sha256_hex,
)


@dataclass(frozen=True)
class KubernetesExecutorConfig:
    api_base_url: str
    allowed_namespaces: frozenset[str]
    allowed_deployments: frozenset[str]
    allowed_operations: frozenset[str] = frozenset({"set_image", "scale", "restart"})
    timeout_seconds: float = 10.0
    max_replicas: int = 100
    max_response_bytes: int = 2 * 1024 * 1024
    executor_identity: str = "spiffe://prodkit.local/executor/kubernetes"


class ConstrainedKubernetesExecutor:
    """Constrained Kubernetes Deployment mutations through the HTTPS API server."""

    name = "kubernetes"
    version = "1.0.0"

    def __init__(
        self,
        config: KubernetesExecutorConfig,
        *,
        credential_resolver: CredentialMaterialResolver,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(config.api_base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Kubernetes API base URL must be absolute HTTPS")
        if not config.allowed_namespaces or not config.allowed_deployments:
            raise ValueError("Kubernetes executor requires namespace and deployment allowlists")
        if config.timeout_seconds <= 0:
            raise ValueError("Kubernetes timeout must be positive")
        if config.max_replicas < 1:
            raise ValueError("Kubernetes max_replicas must be positive")
        self._config = config
        self._resolver = credential_resolver
        self._client = client
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        if action.operation not in self._config.allowed_operations:
            raise PermissionError(f"Kubernetes operation {action.operation!r} is not allowed")
        namespace, deployment = self._target(action)
        if namespace not in self._config.allowed_namespaces:
            raise PermissionError("Kubernetes namespace is not allowlisted")
        if deployment not in self._config.allowed_deployments:
            raise PermissionError("Kubernetes deployment is not allowlisted")
        self._request_spec(action)

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        del action
        raise PermissionError("Kubernetes execution requires a short-lived credential lease")

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        del action, attempt_id
        raise PermissionError("Kubernetes execution requires a short-lived credential lease")

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult:
        await self.validate(action)
        self._validate_lease(action, credential_lease)
        material = await self._resolver.resolve(credential_lease)
        token = material.get("token")
        if not token:
            raise PermissionError("Kubernetes credential lease did not resolve to a bearer token")
        method, path, content_type, payload = self._request_spec(action)
        started = datetime.now(UTC)
        response = await self._request(
            method,
            path,
            token=token,
            content_type=content_type,
            json=payload,
        )
        if len(response.content) > self._config.max_response_bytes:
            raise ValueError("Kubernetes response exceeds configured maximum")
        completed = datetime.now(UTC)
        succeeded = 200 <= response.status_code < 300
        result_payload: dict[str, object] = {
            "status_code": response.status_code,
            "response_sha256": sha256_hex(response.content),
        }
        provider_operation_id: str | None = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                metadata = body.get("metadata")
                if isinstance(metadata, dict):
                    resource_version = metadata.get("resourceVersion")
                    if isinstance(resource_version, str) and resource_version:
                        provider_operation_id = resource_version
                        result_payload["resource_version"] = resource_version
                status = body.get("status")
                if isinstance(status, dict):
                    result_payload["status_sha256"] = sha256_hex(status)
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
            result=result_payload,
            retryable=response.status_code in {409, 429} or response.status_code >= 500,
            error_type=None if succeeded else "KubernetesAPIError",
            error_message=None if succeeded else f"Kubernetes API returned {response.status_code}",
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        namespace, deployment = self._target(action)
        state = {
            "namespace": namespace,
            "deployment": deployment,
            "operation": action.operation,
            "resource_version": result.provider_operation_id,
            "result": result.result,
        }
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )

    def _request_spec(self, action: ActionSpec) -> tuple[str, str, str, dict[str, object]]:
        namespace, deployment = self._target(action)
        base = (
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments/"
            f"{quote(deployment, safe='')}"
        )
        if action.operation == "set_image":
            container = self._required_name(action, "container")
            image = action.arguments.get("image")
            if not isinstance(image, str) or not re.fullmatch(
                r"[^\s@]+@sha256:[0-9a-f]{64}", image
            ):
                raise ValueError("set_image requires an immutable image@sha256:<digest>")
            payload = {
                "spec": {
                    "template": {"spec": {"containers": [{"name": container, "image": image}]}}
                }
            }
            return "PATCH", base, "application/strategic-merge-patch+json", payload
        if action.operation == "scale":
            replicas = action.arguments.get("replicas")
            if (
                not isinstance(replicas, int)
                or isinstance(replicas, bool)
                or replicas < 0
                or replicas > self._config.max_replicas
            ):
                raise ValueError("scale replicas must be an integer within the configured bound")
            return (
                "PATCH",
                f"{base}/scale",
                "application/merge-patch+json",
                {"spec": {"replicas": replicas}},
            )
        if action.operation == "restart":
            restarted_at = action.arguments.get("restarted_at")
            if not isinstance(restarted_at, str) or not restarted_at:
                raise ValueError("restart requires non-empty arguments.restarted_at")
            payload = {
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"prodkit.dev/restartedAt": restarted_at}}
                    }
                }
            }
            return "PATCH", base, "application/strategic-merge-patch+json", payload
        raise PermissionError(f"unsupported Kubernetes operation {action.operation!r}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        content_type: str,
        json: dict[str, object],
    ) -> httpx.Response:
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {token}",
            "content-type": content_type,
            "user-agent": "prodkit-control/0.9",
        }
        url = f"{self._config.api_base_url.rstrip('/')}{path}"
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, json=json)
        async with httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
        ) as client:
            return await client.request(method, url, headers=headers, json=json)

    @staticmethod
    def _required_name(action: ActionSpec, key: str) -> str:
        value = action.arguments.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", value):
            raise ValueError(f"Kubernetes arguments.{key} must be a DNS-compatible name")
        return value

    def _target(self, action: ActionSpec) -> tuple[str, str]:
        namespace = action.arguments.get("namespace")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("Kubernetes action requires arguments.namespace")
        deployment = action.target.resource_id
        if action.target.resource_type not in {"deployment", "kubernetes.deployment"}:
            raise ValueError("Kubernetes executor supports Deployment targets only")
        return namespace, deployment

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this Kubernetes action")
