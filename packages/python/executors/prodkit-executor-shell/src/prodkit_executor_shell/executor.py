from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from prodkit_control_core import ActionSpec, ExecutionResult, StateObservation, sha256_hex


@dataclass(frozen=True)
class ShellExecutorConfig:
    workspace_root: Path
    allowed_executables: frozenset[str]
    allowed_environment_keys: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float = 60.0
    max_output_bytes: int = 1_000_000
    executor_identity: str = "spiffe://prodkit.local/executor/shell"


class ConstrainedShellExecutor:
    name = "shell"
    version = "1.0.0"

    def __init__(self, config: ShellExecutorConfig) -> None:
        self._config = config
        self._root = config.workspace_root.resolve(strict=True)

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        argv = action.arguments.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) for item in argv)
        ):
            raise ValueError("shell actions require a non-empty string argv array")
        executable = Path(argv[0]).name
        if executable not in self._config.allowed_executables:
            raise PermissionError(f"executable {executable!r} is not allow-listed")
        relative_cwd = action.arguments.get("cwd", ".")
        if not isinstance(relative_cwd, str):
            raise ValueError("cwd must be a string")
        cwd = (self._root / relative_cwd).resolve()
        if cwd != self._root and self._root not in cwd.parents:
            raise PermissionError("cwd escapes the configured workspace root")
        requested_env = action.arguments.get("env", {})
        if not isinstance(requested_env, dict):
            raise ValueError("env must be an object")
        rejected = set(requested_env) - self._config.allowed_environment_keys
        if rejected:
            raise PermissionError(f"environment keys are not allow-listed: {sorted(rejected)}")
        env = {
            key: os.environ[key]
            for key in self._config.allowed_environment_keys
            if key in os.environ
        }
        env.update({str(key): str(value) for key, value in requested_env.items()})
        started = datetime.now(UTC)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._config.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            completed = datetime.now(UTC)
            return ExecutionResult(
                action_id=action.action_id,
                execution_attempt_id=uuid4(),
                executor_name=self.name,
                executor_version=self.version,
                executor_identity=self._config.executor_identity,
                started_at=started,
                completed_at=completed,
                succeeded=False,
                retryable=True,
                error_type="timeout",
                error_message=f"command exceeded {self._config.timeout_seconds} seconds",
            )
        stdout = stdout[: self._config.max_output_bytes]
        stderr = stderr[: self._config.max_output_bytes]
        completed = datetime.now(UTC)
        succeeded = process.returncode == 0
        return ExecutionResult(
            action_id=action.action_id,
            execution_attempt_id=uuid4(),
            executor_name=self.name,
            executor_version=self.version,
            executor_identity=self._config.executor_identity,
            started_at=started,
            completed_at=completed,
            succeeded=succeeded,
            exit_code=process.returncode,
            result={
                "argv": argv,
                "cwd": str(cwd.relative_to(self._root)),
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            },
            retryable=False,
            error_type=None if succeeded else "nonzero_exit",
            error_message=None if succeeded else f"command exited with {process.returncode}",
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        state = {
            "execution_attempt_id": str(result.execution_attempt_id),
            "exit_code": result.exit_code,
            "result_digest": sha256_hex(result.result),
        }
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )
