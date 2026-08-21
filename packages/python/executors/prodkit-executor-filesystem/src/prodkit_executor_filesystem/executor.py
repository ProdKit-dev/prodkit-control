from __future__ import annotations

import asyncio
import base64
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActionSpec,
    CredentialLease,
    ExecutionResult,
    IntegrityViolationError,
    StateObservation,
    sha256_hex,
)


@dataclass(frozen=True)
class FilesystemExecutorConfig:
    root: Path
    allowed_operations: frozenset[str] = frozenset({"write", "delete", "mkdir"})
    max_write_bytes: int = 8 * 1024 * 1024
    allow_delete: bool = False
    executor_identity: str = "spiffe://prodkit.local/executor/filesystem"


class ConstrainedFilesystemExecutor:
    """Path-confined filesystem mutations with digest preconditions and atomic writes."""

    name = "filesystem"
    version = "1.0.0"

    def __init__(self, config: FilesystemExecutorConfig) -> None:
        self._config = config
        self._root = config.root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        if action.operation not in self._config.allowed_operations:
            raise PermissionError(f"filesystem operation {action.operation!r} is not allowed")
        path = self._path(action)
        if action.operation == "delete":
            if not self._config.allow_delete:
                raise PermissionError("filesystem delete capability is disabled")
            if action.target.expected_pre_state_digest is None:
                raise ValueError("filesystem delete requires expected_pre_state_digest")
        if action.operation == "write":
            content = self._content(action)
            if len(content) > self._config.max_write_bytes:
                raise ValueError("filesystem write exceeds configured maximum size")
        if action.target.resource_id not in {str(path.relative_to(self._root)), path.name}:
            raise ValueError("filesystem target resource_id does not match requested path")

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        return await self.execute_attempt(action, attempt_id=uuid4())

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        await self.validate(action)
        return await self._execute(action, attempt_id=attempt_id)

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult:
        self._validate_lease(action, credential_lease)
        return await self.execute_attempt(action, attempt_id=attempt_id)

    async def _execute(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        path = self._path(action)
        started = datetime.now(UTC)
        await asyncio.to_thread(self._assert_precondition, action, path)
        if action.operation == "write":
            content = self._content(action)
            await asyncio.to_thread(self._atomic_write, path, content)
        elif action.operation == "delete":
            await asyncio.to_thread(path.unlink)
        elif action.operation == "mkdir":
            await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        else:  # validate() has already rejected unsupported operations
            raise AssertionError("unreachable filesystem operation")
        state = await asyncio.to_thread(self._state, path)
        completed = datetime.now(UTC)
        return ExecutionResult(
            action_id=action.action_id,
            execution_attempt_id=attempt_id,
            executor_name=self.name,
            executor_version=self.version,
            executor_identity=self.identity,
            started_at=started,
            completed_at=completed,
            succeeded=True,
            exit_code=0,
            result={"path": str(path.relative_to(self._root)), "state": state},
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        del result
        path = self._path(action)
        state = await asyncio.to_thread(self._state, path)
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )

    def _path(self, action: ActionSpec) -> Path:
        raw = action.arguments.get("path")
        if not isinstance(raw, str) or not raw:
            raise ValueError("filesystem action requires string arguments.path")
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("filesystem path must be relative and cannot traverse parents")
        resolved = (self._root / relative).resolve()
        if resolved != self._root and self._root not in resolved.parents:
            raise PermissionError("filesystem path escapes configured root")
        return resolved

    def _content(self, action: ActionSpec) -> bytes:
        raw_base64 = action.arguments.get("content_base64")
        raw_text = action.arguments.get("content")
        if raw_base64 is not None and raw_text is not None:
            raise ValueError("provide either content or content_base64, not both")
        if isinstance(raw_base64, str):
            try:
                return base64.b64decode(raw_base64, validate=True)
            except ValueError as exc:
                raise ValueError("content_base64 is invalid") from exc
        if isinstance(raw_text, str):
            return raw_text.encode("utf-8")
        raise ValueError("filesystem write requires content or content_base64")

    def _assert_precondition(self, action: ActionSpec, path: Path) -> None:
        expected = action.target.expected_pre_state_digest
        if expected is None:
            return
        if not path.is_file():
            raise IntegrityViolationError("filesystem pre-state target does not exist as a file")
        actual = sha256_hex(path.read_bytes())
        if actual != expected:
            raise IntegrityViolationError("filesystem pre-state digest mismatch")

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _state(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"exists": False}
        if path.is_dir():
            return {"exists": True, "kind": "directory"}
        content = path.read_bytes()
        return {
            "exists": True,
            "kind": "file",
            "size_bytes": len(content),
            "sha256": sha256_hex(content),
        }

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this filesystem action")
