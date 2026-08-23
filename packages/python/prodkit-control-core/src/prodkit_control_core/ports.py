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
    DurableWorkItem,
    ExecutionAttemptRecord,
    ExecutionResult,
    FencedLease,
    LeasedWorkItem,
    LineageGraph,
    LineageNode,
    LineageRelation,
    PolicyDecision,
    QueueSnapshot,
    ReconciliationFinding,
    RetentionCandidate,
    RetentionDecision,
    RunRecord,
    StateObservation,
    TenantAccessContext,
    VerificationResult,
)


@runtime_checkable
class EventLedger(Protocol):
    async def append(self, draft: ControlEventDraft) -> ControlEvent: ...
    async def list_run_events(self, *, tenant_id: str, run_id: UUID) -> list[ControlEvent]: ...
    def stream_run_events(self, *, tenant_id: str, run_id: UUID) -> AsyncIterator[ControlEvent]: ...
    async def verify_run(self, *, tenant_id: str, run_id: UUID) -> None: ...


@runtime_checkable
class RunStore(Protocol):
    async def create(self, run: RunRecord) -> None: ...
    async def replace(self, run: RunRecord) -> None: ...
    async def get(self, *, tenant_id: str, run_id: UUID) -> RunRecord | None: ...


@runtime_checkable
class LineageStore(Protocol):
    async def record_node(self, node: LineageNode) -> None: ...
    async def record_relation(
        self, *, tenant_id: str, run_id: UUID, relation: LineageRelation
    ) -> None: ...
    async def get_graph(self, *, tenant_id: str, run_id: UUID) -> LineageGraph: ...


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
    async def get(self, *, tenant_id: str, artifact: ArtifactRef) -> bytes: ...


@runtime_checkable
class RetentionDeletionAdapter(Protocol):
    """Bounded deletion effect that consumes the exact governed retention decision."""

    async def delete(
        self,
        *,
        context: TenantAccessContext,
        candidate: RetentionCandidate,
        decision: RetentionDecision,
    ) -> str: ...


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
class ApprovalRecorder(Protocol):
    async def record(self, decision: ApprovalDecision) -> None: ...


@runtime_checkable
class ControlledExecutor(Protocol):
    name: str
    version: str

    async def execute(self, action: ActionSpec) -> ExecutionResult: ...
    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation: ...


@runtime_checkable
class PreflightExecutor(ControlledExecutor, Protocol):
    """Executor that can reject unsafe/invalid actions before idempotency ownership starts."""

    async def validate(self, action: ActionSpec) -> None: ...


@runtime_checkable
class AttemptAwareExecutor(ControlledExecutor, Protocol):
    """Executor capable of using a broker-owned durable attempt identifier."""

    identity: str

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult: ...


@runtime_checkable
class CredentialLeaseAwareExecutor(AttemptAwareExecutor, Protocol):
    """Isolated executor that consumes only a short-lived credential lease reference."""

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult: ...


@runtime_checkable
class CredentialMaterialResolver(Protocol):
    """Worker-local resolver; credential material never crosses the control-plane API boundary."""

    async def resolve(self, lease: CredentialLease) -> Mapping[str, str]: ...


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
    """Permanent external-effect ownership; claims are never lease-expired for replay."""

    async def claim(self, *, tenant_id: str, key: str, action_digest: str) -> bool: ...
    async def complete(self, *, tenant_id: str, key: str, result: ExecutionResult) -> None: ...
    async def result(self, *, tenant_id: str, key: str) -> ExecutionResult | None: ...


@runtime_checkable
class ExecutionAttemptStore(Protocol):
    async def create(self, attempt: ExecutionAttemptRecord) -> None: ...
    async def replace(self, attempt: ExecutionAttemptRecord) -> None: ...
    async def get(self, *, tenant_id: str, attempt_id: UUID) -> ExecutionAttemptRecord | None: ...
    async def latest_for_action(
        self, *, tenant_id: str, action_id: UUID
    ) -> ExecutionAttemptRecord | None: ...


@runtime_checkable
class CredentialLeaseProvider(Protocol):
    async def issue(self, *, action: ActionSpec, executor_identity: str) -> CredentialLease: ...
    async def revoke(self, lease_id: UUID) -> None: ...


@runtime_checkable
class LeaseStore(Protocol):
    """Reusable exclusive ownership with monotonic fencing across expiry and release."""

    async def acquire(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> FencedLease | None: ...

    async def renew(self, lease: FencedLease, *, ttl_seconds: float) -> FencedLease: ...
    async def release(self, lease: FencedLease) -> None: ...
    async def is_current(self, lease: FencedLease) -> bool: ...


@runtime_checkable
class DurableWorkQueue(Protocol):
    """Bounded recoverable work queue whose ordinary access is always tenant scoped."""

    async def enqueue(self, item: DurableWorkItem) -> DurableWorkItem: ...

    async def acquire(
        self,
        *,
        queue: str,
        owner_id: str,
        lease_ttl_seconds: float,
        tenant_id: str,
    ) -> LeasedWorkItem | None: ...

    async def complete(self, leased: LeasedWorkItem) -> DurableWorkItem: ...

    async def retry(
        self,
        leased: LeasedWorkItem,
        *,
        delay_seconds: float,
        error: str,
    ) -> DurableWorkItem: ...

    async def snapshot(self, *, queue: str, tenant_id: str) -> QueueSnapshot: ...


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse: ...
