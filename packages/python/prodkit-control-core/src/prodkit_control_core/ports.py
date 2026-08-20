"""Dependency-inversion protocols for storage, policy, approval, execution, and evidence."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol, runtime_checkable
from uuid import UUID

from .contracts import (
    ActionSpec,
    ApprovalDecision,
    ArtifactRef,
    CanonicalModelRequest,
    CanonicalModelResponse,
    ControlEvent,
    ControlEventDraft,
    CredentialLease,
    ExecutionAttemptRecord,
    ExecutionResult,
    LineageGraph,
    LineageNode,
    LineageRelation,
    PolicyDecision,
    ReconciliationFinding,
    StateObservation,
    VerificationResult,
)


@runtime_checkable
class EventLedger(Protocol):
    async def append(self, draft: ControlEventDraft) -> ControlEvent: ...
    async def list_run_events(self, run_id: UUID) -> list[ControlEvent]: ...
    def stream_run_events(self, run_id: UUID) -> AsyncIterator[ControlEvent]: ...
    async def verify_run(self, run_id: UUID) -> None: ...


@runtime_checkable
class LineageStore(Protocol):
    async def record_node(self, node: LineageNode) -> None: ...
    async def record_relation(self, run_id: UUID, relation: LineageRelation) -> None: ...
    async def get_graph(self, run_id: UUID) -> LineageGraph: ...


@runtime_checkable
class ArtifactStore(Protocol):
    async def put(
        self,
        *,
        tenant_id: str,
        media_type: str,
        content: bytes,
        classification: str = "internal",
        redact: bool = False,
    ) -> ArtifactRef: ...
    async def get(self, artifact: ArtifactRef) -> bytes: ...


@runtime_checkable
class PolicyEngine(Protocol):
    async def evaluate(self, action: ActionSpec) -> PolicyDecision: ...


@runtime_checkable
class ApprovalProvider(Protocol):
    async def find_valid_approval(
        self,
        *,
        action: ActionSpec,
        policy_decision: PolicyDecision,
        target_digest: str,
    ) -> ApprovalDecision | None: ...


@runtime_checkable
class ControlledExecutor(Protocol):
    name: str
    version: str

    async def execute(self, action: ActionSpec) -> ExecutionResult: ...
    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation: ...


@runtime_checkable
class AttemptAwareExecutor(ControlledExecutor, Protocol):
    """Executor capable of using a broker-owned durable attempt identifier."""

    identity: str

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult: ...


@runtime_checkable
class EffectVerifier(Protocol):
    name: str
    version: str

    async def verify(
        self,
        action: ActionSpec,
        result: ExecutionResult,
        observation: StateObservation,
    ) -> VerificationResult: ...


@runtime_checkable
class Reconciler(Protocol):
    name: str

    async def reconcile(
        self,
        run_id: UUID,
        actions: Mapping[UUID, ActionSpec],
    ) -> list[ReconciliationFinding]: ...


@runtime_checkable
class IdempotencyStore(Protocol):
    async def claim(self, *, tenant_id: str, key: str, action_digest: str) -> bool: ...
    async def complete(self, *, tenant_id: str, key: str, result: ExecutionResult) -> None: ...
    async def result(self, *, tenant_id: str, key: str) -> ExecutionResult | None: ...


@runtime_checkable
class ExecutionAttemptStore(Protocol):
    async def create(self, attempt: ExecutionAttemptRecord) -> None: ...
    async def replace(self, attempt: ExecutionAttemptRecord) -> None: ...
    async def get(self, attempt_id: UUID) -> ExecutionAttemptRecord | None: ...
    async def latest_for_action(self, action_id: UUID) -> ExecutionAttemptRecord | None: ...


@runtime_checkable
class CredentialLeaseProvider(Protocol):
    async def issue(self, *, action: ActionSpec, executor_identity: str) -> CredentialLease: ...
    async def revoke(self, lease_id: UUID) -> None: ...


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse: ...
