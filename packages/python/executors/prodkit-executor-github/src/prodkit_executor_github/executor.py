from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
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
class GitHubExecutorConfig:
    allowed_repositories: frozenset[str]
    api_base_url: str = "https://api.github.com"
    allowed_operations: frozenset[str] = frozenset(
        {"create_pull_request", "merge_pull_request", "create_ref", "dispatch_workflow", "create_release"}
    )
    allowed_merge_methods: frozenset[str] = frozenset({"squash", "merge", "rebase"})
    timeout_seconds: float = 10.0
    max_response_bytes: int = 2 * 1024 * 1024
    executor_identity: str = "spiffe://prodkit.local/executor/github"


class ConstrainedGitHubExecutor:
    """Allowlisted GitHub REST mutations with exact mutation-specific preconditions."""

    name = "github"
    version = "1.0.0"

    def __init__(
        self,
        config: GitHubExecutorConfig,
        *,
        credential_resolver: CredentialMaterialResolver,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(config.api_base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("GitHub API base URL must be absolute HTTPS")
        if not config.allowed_repositories:
            raise ValueError("GitHub executor requires an explicit repository allowlist")
        self._config = config
        self._resolver = credential_resolver
        self._client = client
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        if action.operation not in self._config.allowed_operations:
            raise PermissionError(f"GitHub operation {action.operation!r} is not allowed")
        repository = self._repository(action)
        if repository not in self._config.allowed_repositories:
            raise PermissionError(f"GitHub repository {repository!r} is not allowed")
        if action.target.resource_id != repository:
            raise ValueError("GitHub target resource_id must equal arguments.repository")
        self._request_spec(action)

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        raise PermissionError("GitHub mutations require a short-lived credential lease")

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        raise PermissionError("GitHub mutations require a short-lived credential lease")

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
            raise PermissionError("GitHub credential lease did not resolve to a token")
        method, path, payload = self._request_spec(action)
        started = datetime.now(UTC)
        response = await self._request(method, path, token=token, json=payload)
        if len(response.content) > self._config.max_response_bytes:
            raise ValueError("GitHub response exceeds configured maximum")
        completed = datetime.now(UTC)
        succeeded = 200 <= response.status_code < 300
        result_payload: dict[str, object] = {
            "status_code": response.status_code,
            "response_sha256": sha256_hex(response.content),
        }
        provider_operation_id: str | None = None
        resource_path: str | None = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                identifier = body.get("id") or body.get("number")
                if identifier is not None:
                    provider_operation_id = str(identifier)
                resource_url = body.get("url")
                if isinstance(resource_url, str) and resource_url.startswith(self._config.api_base_url):
                    resource_path = resource_url.removeprefix(self._config.api_base_url)
                    result_payload["resource_path"] = resource_path
                sha = body.get("sha")
                if isinstance(sha, str):
                    result_payload["sha"] = sha
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
            retryable=(response.status_code == 429 or response.status_code >= 500),
            error_type=None if succeeded else "GitHubAPIError",
            error_message=None if succeeded else f"GitHub API returned {response.status_code}",
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        resource_path = result.result.get("resource_path")
        if isinstance(resource_path, str):
            state: dict[str, object] = {"resource_path": resource_path, "provider_operation_id": result.provider_operation_id}
        else:
            state = {"operation": action.operation, "result": result.result}
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )

    def _request_spec(self, action: ActionSpec) -> tuple[str, str, dict[str, object]]:
        repository = self._repository(action)
        arguments = action.arguments
        prefix = f"/repos/{repository}"
        if action.operation == "create_pull_request":
            payload = {
                "title": self._required_str(arguments, "title"),
                "head": self._required_str(arguments, "head"),
                "base": self._required_str(arguments, "base"),
                "body": str(arguments.get("body", "")),
                "draft": bool(arguments.get("draft", True)),
            }
            return "POST", f"{prefix}/pulls", payload
        if action.operation == "merge_pull_request":
            number = self._required_int(arguments, "pull_number")
            expected_sha = self._required_str(arguments, "expected_head_sha")
            method = str(arguments.get("merge_method", "squash"))
            if method not in self._config.allowed_merge_methods:
                raise PermissionError(f"GitHub merge method {method!r} is not allowed")
            return "PUT", f"{prefix}/pulls/{number}/merge", {"sha": expected_sha, "merge_method": method}
        if action.operation == "create_ref":
            ref = self._required_str(arguments, "ref")
            if not ref.startswith("refs/") or ".." in ref:
                raise ValueError("GitHub ref must be a canonical refs/* value")
            sha = self._required_str(arguments, "sha")
            return "POST", f"{prefix}/git/refs", {"ref": ref, "sha": sha}
        if action.operation == "dispatch_workflow":
            workflow = self._required_str(arguments, "workflow")
            ref = self._required_str(arguments, "ref")
            raw_inputs = arguments.get("inputs", {})
            if not isinstance(raw_inputs, dict):
                raise ValueError("GitHub workflow inputs must be an object")
            return "POST", f"{prefix}/actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": raw_inputs}
        if action.operation == "create_release":
            tag = self._required_str(arguments, "tag_name")
            name = self._required_str(arguments, "name")
            payload = {
                "tag_name": tag,
                "name": name,
                "draft": bool(arguments.get("draft", False)),
                "prerelease": bool(arguments.get("prerelease", False)),
            }
            return "POST", f"{prefix}/releases", payload
        raise PermissionError(f"unsupported GitHub operation {action.operation!r}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        json: dict[str, object],
    ) -> httpx.Response:
        headers = {
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {token}",
            "x-github-api-version": "2022-11-28",
            "user-agent": "prodkit-control/0.1",
        }
        url = f"{self._config.api_base_url.rstrip('/')}{path}"
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, json=json)
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds, follow_redirects=False) as client:
            return await client.request(method, url, headers=headers, json=json)

    @staticmethod
    def _required_str(arguments: dict[str, object], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"GitHub action requires string arguments.{key}")
        return value

    @staticmethod
    def _required_int(arguments: dict[str, object], key: str) -> int:
        value = arguments.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"GitHub action requires positive integer arguments.{key}")
        return value

    def _repository(self, action: ActionSpec) -> str:
        repository = action.arguments.get("repository")
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise ValueError("GitHub action requires owner/name arguments.repository")
        return repository

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this GitHub action")
