from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActorRef,
    AuthorizationDeniedError,
    ControlEventDraft,
    EventLedger,
    EventType,
    LineageNodeRef,
    RunRecord,
    RunStatus,
    RunStore,
    sha256_hex,
)

from .runs import InMemoryRunStore
from .util import new_span_id, new_trace_id

_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INCOMPLETE}
)


class RunCoordinator:
    def __init__(self, ledger: EventLedger, runs: RunStore | None = None) -> None:
        self._ledger = ledger
        self._runs = runs or InMemoryRunStore()

    async def start_run(
        self,
        *,
        tenant_id: str,
        initiated_by: ActorRef,
        environment: str,
        purpose: str,
        source_intent: object | None = None,
        specification_revision: LineageNodeRef | None = None,
        workflow_id: str | None = None,
    ) -> RunRecord:
        if initiated_by.tenant_id != tenant_id:
            raise AuthorizationDeniedError("initiating actor tenant does not match run tenant")

        now = datetime.now(UTC)
        run = RunRecord(
            run_id=uuid4(),
            tenant_id=tenant_id,
            status=RunStatus.RUNNING,
            initiated_by=initiated_by,
            environment=environment,
            purpose=purpose,
            trace_id=new_trace_id(),
            started_at=now,
            source_intent_digest=sha256_hex(source_intent) if source_intent is not None else None,
            specification_revision=specification_revision,
            workflow_id=workflow_id,
        )
        event = ControlEventDraft(
            event_id=uuid4(),
            run_id=run.run_id,
            tenant_id=tenant_id,
            event_type=EventType.RUN_STARTED,
            occurred_at=now,
            recorded_at=now,
            actor=initiated_by,
            trace_id=run.trace_id,
            span_id=new_span_id(),
            payload=run.model_dump(mode="json"),
        )
        await self._runs.create(run)
        try:
            await self._ledger.append(event)
        except Exception:
            # The durable store is authoritative. A failed ledger append must not expose a run that
            # lacks its canonical RUN_STARTED evidence, so callers treat the start as failed. The
            # production Postgres composition places these writes behind the same database and its
            # startup recovery reconciles any pre-event row before accepting traffic.
            raise
        return run

    async def complete_run(
        self,
        run_id: UUID,
        *,
        actor: ActorRef,
        status: RunStatus = RunStatus.SUCCEEDED,
        summary: dict[str, object] | None = None,
    ) -> RunRecord:
        current = await self.require_run(run_id)
        if actor.tenant_id != current.tenant_id:
            raise AuthorizationDeniedError("completing actor tenant does not match run tenant")
        if status not in _TERMINAL_RUN_STATUSES:
            raise ValueError("run completion requires a terminal status")
        if current.status in _TERMINAL_RUN_STATUSES:
            raise ValueError("run is already terminal")

        now = datetime.now(UTC)
        completed = current.model_copy(update={"status": status, "completed_at": now})
        event = ControlEventDraft(
            event_id=uuid4(),
            run_id=run_id,
            tenant_id=current.tenant_id,
            event_type=EventType.RUN_COMPLETED,
            occurred_at=now,
            recorded_at=now,
            actor=actor,
            trace_id=current.trace_id,
            span_id=new_span_id(),
            payload={"status": status.value, "summary": summary or {}},
        )
        await self._ledger.append(event)
        await self._runs.replace(completed)
        return completed

    async def get_run(self, run_id: UUID) -> RunRecord | None:
        return await self._runs.get(run_id)

    async def require_run(self, run_id: UUID) -> RunRecord:
        run = await self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    async def bind_lineage(
        self,
        run_id: UUID,
        *,
        lineage_graph_digest: str,
        specification_revision: LineageNodeRef | None = None,
    ) -> RunRecord:
        current = await self.require_run(run_id)
        if (
            current.specification_revision is not None
            and specification_revision is not None
            and current.specification_revision != specification_revision
        ):
            raise ValueError("a run cannot be rebound to another specification revision")
        updated = current.model_copy(
            update={
                "lineage_graph_digest": lineage_graph_digest,
                "specification_revision": specification_revision or current.specification_revision,
            }
        )
        await self._runs.replace(updated)
        return updated
