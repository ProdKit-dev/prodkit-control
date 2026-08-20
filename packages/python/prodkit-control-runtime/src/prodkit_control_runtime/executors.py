from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActionSpec,
    ExecutionResult,
    ExecutorNotFoundError,
    StateObservation,
    VerificationOutcome,
    VerificationResult,
    sha256_hex,
)


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Any] = {}

    def register(self, executor: Any) -> None:
        if executor.name in self._executors:
            raise ValueError(f"executor {executor.name!r} already registered")
        self._executors[executor.name] = executor

    def get(self, name: str) -> Any:
        try:
            return self._executors[name]
        except KeyError as exc:
            raise ExecutorNotFoundError(f"no controlled executor named {name!r}") from exc


class DryRunExecutor:
    """Deterministic executor that records the intended effect without external side effects."""

    name = "dry-run"
    version = "1.0.0"
    identity = "spiffe://prodkit.local/executor/dry-run"

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        return await self.execute_attempt(action, attempt_id=uuid4())

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        started = datetime.now(UTC)
        result = {
            "dry_run": True,
            "operation": action.operation,
            "target": action.target.model_dump(mode="json"),
            "arguments_digest": sha256_hex(action.arguments),
            "expected_effect": action.expected_effect,
        }
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
            result=result,
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        state = {
            "target": action.target.model_dump(mode="json"),
            "effect": result.result.get("expected_effect", {}),
            "dry_run": True,
        }
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source="dry-run-executor",
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )


class DigestEffectVerifier:
    """Reference verifier comparing a deterministic expected observation digest."""

    name = "digest-effect-verifier"
    version = "1.0.0"

    async def verify(
        self,
        action: ActionSpec,
        result: ExecutionResult,
        observation: StateObservation,
    ) -> VerificationResult:
        expected_state = {
            "target": action.target.model_dump(mode="json"),
            "effect": action.expected_effect,
            "dry_run": result.result.get("dry_run", False),
        }
        expected_digest = sha256_hex(expected_state)
        passed = expected_digest == observation.state_digest and result.succeeded
        return VerificationResult(
            verification_id=uuid4(),
            action_id=action.action_id,
            verifier=self.name,
            verifier_version=self.version,
            verified_at=datetime.now(UTC),
            outcome=VerificationOutcome.PASSED if passed else VerificationOutcome.FAILED,
            expected_digest=expected_digest,
            observed_digest=observation.state_digest,
            checks=("result_succeeded", "expected_observation_digest_matches"),
            details={"result_succeeded": result.succeeded},
        )
