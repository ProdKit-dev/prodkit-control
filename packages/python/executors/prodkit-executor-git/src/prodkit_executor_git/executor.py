from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActionSpec,
    CredentialLease,
    CredentialMaterialResolver,
    ExecutionResult,
    StateObservation,
    sha256_hex,
)


@dataclass(frozen=True)
class GitExecutorConfig:
    workspace_root: Path
    allowed_operations: frozenset[str] = frozenset({"update_ref", "tag", "commit", "push"})
    allowed_remotes: frozenset[str] = frozenset({"origin"})
    allowed_ref_prefixes: tuple[str, ...] = ("refs/heads/", "refs/tags/")
    credential_env_keys: frozenset[str] = frozenset(
        {"GIT_ASKPASS", "GIT_TERMINAL_PROMPT", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND"}
    )
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1024 * 1024
    executor_identity: str = "spiffe://prodkit.local/executor/git"


class ConstrainedGitExecutor:
    """Explicit Git mutations without shell evaluation or arbitrary argv passthrough."""

    name = "git"
    version = "1.0.0"

    def __init__(
        self,
        config: GitExecutorConfig,
        *,
        credential_resolver: CredentialMaterialResolver | None = None,
    ) -> None:
        self._config = config
        self._root = config.workspace_root.resolve()
        self._resolver = credential_resolver
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        if action.operation not in self._config.allowed_operations:
            raise PermissionError(f"Git operation {action.operation!r} is not allowed")
        repo = self._repo(action)
        if not (repo / ".git").exists():
            raise ValueError("Git target is not a working-tree repository")
        if action.target.resource_id != str(repo.relative_to(self._root)):
            raise ValueError("Git target resource_id must equal arguments.repo")
        self._command(action)

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        return await self.execute_attempt(action, attempt_id=uuid4())

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        await self.validate(action)
        return await self._execute(action, attempt_id=attempt_id, credential_env={})

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult:
        self._validate_lease(action, credential_lease)
        material: dict[str, str] = {}
        if self._resolver is not None:
            resolved = await self._resolver.resolve(credential_lease)
            for key, value in resolved.items():
                if key not in self._config.credential_env_keys:
                    raise PermissionError(f"credential environment key {key!r} is not allowed")
                material[key] = value
        elif action.operation == "push":
            raise PermissionError("Git push requires a worker-local credential resolver")
        return await self._execute(action, attempt_id=attempt_id, credential_env=material)

    async def _execute(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_env: dict[str, str],
    ) -> ExecutionResult:
        repo = self._repo(action)
        commands = self._command(action)
        started = datetime.now(UTC)
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        exit_code = 0
        for args in commands:
            exit_code, stdout, stderr = await self._run(repo, args, credential_env)
            stdout_parts.append(stdout)
            stderr_parts.append(stderr)
            if exit_code != 0:
                break
        completed = datetime.now(UTC)
        stdout = b"".join(stdout_parts)[-self._config.max_output_bytes :]
        stderr = b"".join(stderr_parts)[-self._config.max_output_bytes :]
        succeeded = exit_code == 0
        state = await self._observe_state(repo)
        return ExecutionResult(
            action_id=action.action_id,
            execution_attempt_id=attempt_id,
            executor_name=self.name,
            executor_version=self.version,
            executor_identity=self.identity,
            started_at=started,
            completed_at=completed,
            succeeded=succeeded,
            exit_code=exit_code,
            result={
                "head": state["head"],
                "status_sha256": state["status_sha256"],
                "stdout_sha256": sha256_hex(stdout),
                "stderr_sha256": sha256_hex(stderr),
            },
            retryable=False,
            error_type=None if succeeded else "GitCommandError",
            error_message=None if succeeded else "controlled Git command failed",
        )

    async def observe(self, action: ActionSpec, _result: ExecutionResult) -> StateObservation:
        state = await self._observe_state(self._repo(action))
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )

    def _repo(self, action: ActionSpec) -> Path:
        raw = action.arguments.get("repo")
        if not isinstance(raw, str) or not raw:
            raise ValueError("Git action requires arguments.repo")
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("Git repository path must stay inside the workspace")
        repo = (self._root / relative).resolve()
        if repo != self._root and self._root not in repo.parents:
            raise PermissionError("Git repository escapes workspace root")
        return repo

    def _command(self, action: ActionSpec) -> list[list[str]]:
        args = action.arguments
        if action.operation == "update_ref":
            ref = self._required_str(args, "ref")
            self._validate_ref(ref)
            new_oid = self._required_str(args, "new_oid")
            old_oid = self._required_str(args, "old_oid")
            return [["git", "update-ref", ref, new_oid, old_oid]]
        if action.operation == "tag":
            tag = self._required_str(args, "tag")
            ref = f"refs/tags/{tag}"
            self._validate_ref(ref)
            target = self._required_str(args, "target")
            message = self._required_str(args, "message")
            return [["git", "tag", "-a", tag, target, "-m", message]]
        if action.operation == "commit":
            paths = args.get("paths")
            message = self._required_str(args, "message")
            if (
                not isinstance(paths, list)
                or not paths
                or not all(isinstance(path, str) for path in paths)
            ):
                raise ValueError("Git commit requires a non-empty string list arguments.paths")
            if any(path.startswith("-") or ".." in Path(path).parts for path in paths):
                raise PermissionError("Git commit paths contain an unsafe path")
            return [["git", "add", "--", *paths], ["git", "commit", "-m", message]]
        if action.operation == "push":
            remote = self._required_str(args, "remote")
            refspec = self._required_str(args, "refspec")
            if remote not in self._config.allowed_remotes:
                raise PermissionError(f"Git remote {remote!r} is not allowed")
            if ":" in refspec:
                destination = refspec.split(":", 1)[1]
                self._validate_ref(destination)
            return [["git", "push", "--porcelain", remote, refspec]]
        raise PermissionError(f"unsupported Git operation {action.operation!r}")

    async def _run(
        self,
        repo: Path,
        args: list[str],
        credential_env: dict[str, str],
    ) -> tuple[int, bytes, bytes]:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        env.update(credential_env)
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=repo,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError("controlled Git command timed out") from None
        if (
            len(stdout) > self._config.max_output_bytes
            or len(stderr) > self._config.max_output_bytes
        ):
            raise ValueError("Git command output exceeded configured maximum")
        return process.returncode or 0, stdout, stderr

    async def _observe_state(self, repo: Path) -> dict[str, object]:
        head_code, head, _ = await self._run(repo, ["git", "rev-parse", "HEAD"], {})
        status_code, status, _ = await self._run(repo, ["git", "status", "--porcelain=v1"], {})
        if head_code != 0 or status_code != 0:
            raise RuntimeError("unable to observe Git repository state")
        return {
            "head": head.decode().strip(),
            "status_sha256": sha256_hex(status),
            "dirty": bool(status.strip()),
        }

    def _validate_ref(self, ref: str) -> None:
        if not any(ref.startswith(prefix) for prefix in self._config.allowed_ref_prefixes):
            raise PermissionError(f"Git ref {ref!r} is outside the configured prefixes")
        if ".." in ref or ref.endswith("/") or " " in ref:
            raise ValueError("Git ref is invalid")

    @staticmethod
    def _required_str(arguments: dict[str, object], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Git action requires string arguments.{key}")
        return value

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this Git action")
