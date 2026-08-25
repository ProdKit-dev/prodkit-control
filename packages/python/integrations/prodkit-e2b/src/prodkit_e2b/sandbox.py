from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from prodkit_control_core import sha256_hex


@dataclass(frozen=True)
class SandboxExecution:
    sandbox_id: str
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class SandboxEvidence:
    sandbox_id: str
    exit_code: int
    succeeded: bool
    stdout_sha256: str
    stderr_sha256: str


class E2BClient(Protocol):
    async def run(
        self,
        *,
        template: str,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> SandboxExecution: ...


@dataclass(frozen=True)
class E2BSandboxConfig:
    allowed_templates: frozenset[str]
    allowed_command_sha256: frozenset[str]
    allowed_environment_keys: frozenset[str] = frozenset()
    timeout_seconds: float = 60.0
    max_output_bytes: int = 2 * 1024 * 1024


class E2BSandboxAdapter:
    """Constrain E2B sandbox execution while emitting content-addressed evidence."""

    def __init__(self, config: E2BSandboxConfig, *, client: E2BClient) -> None:
        if not config.allowed_templates:
            raise ValueError("E2B adapter requires an explicit template allowlist")
        if not config.allowed_command_sha256:
            raise ValueError("E2B adapter requires explicit command digests")
        if config.timeout_seconds <= 0 or config.max_output_bytes < 1:
            raise ValueError("E2B timeout/output limits must be positive")
        self._config = config
        self._client = client

    async def run(
        self,
        *,
        template: str,
        command: tuple[str, ...],
        environment: Mapping[str, str] | None = None,
    ) -> SandboxEvidence:
        if template not in self._config.allowed_templates:
            raise PermissionError("E2B sandbox template is not allowlisted")
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("E2B command must be a non-empty tuple of non-empty strings")
        if sha256_hex(command) not in self._config.allowed_command_sha256:
            raise PermissionError("E2B command is not allowlisted")
        env = dict(environment or {})
        unknown = set(env) - self._config.allowed_environment_keys
        if unknown:
            raise PermissionError(f"E2B environment keys are not allowlisted: {sorted(unknown)}")
        execution = await self._client.run(
            template=template,
            command=command,
            environment=env,
            timeout_seconds=self._config.timeout_seconds,
        )
        if not execution.sandbox_id:
            raise ValueError("E2B execution did not return a sandbox id")
        if (
            len(execution.stdout) > self._config.max_output_bytes
            or len(execution.stderr) > self._config.max_output_bytes
        ):
            raise ValueError("E2B output exceeds configured evidence bound")
        return SandboxEvidence(
            sandbox_id=execution.sandbox_id,
            exit_code=execution.exit_code,
            succeeded=execution.exit_code == 0,
            stdout_sha256=sha256_hex(execution.stdout),
            stderr_sha256=sha256_hex(execution.stderr),
        )
