from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActorRef,
    EventLedger,
    EventType,
    LineageNodeRef,
    ControlEventDraft,
    RunRecord,
    RunStatus,
    sha256_hex,
)

from .util import new_span_id, new_trace_id


class RunCoordinator:
    def __init__(self, ledger: EventLedger) -> None:
        self._ledger = ledger
        self._runs: dict[UUID, RunRecord] = {}

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
        self._runs[run.run_id] = run
        await self._ledger.append(
            ControlEventDraft(
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
        )
        return run

    async def complete_run(
        self,
        run_id: UUID,
        *,
        actor: ActorRef,
        status: RunStatus = RunStatus.SUCCEEDED,
        summary: dict[str, object] | None = None,
    ) -> RunRecord:
        current = self._runs[run_id]
        now = datetime.now(UTC)
        completed = current.model_copy(update={"status": status, "completed_at": now})
        self._runs[run_id] = completed
        await self._ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=run_id,
                tenant_id=current.tenant_id,
                event_type=EventType.RUN_COMPLETED,
                occurred_at=now,
                recorded_at=now,
                actor=actor,
                trace_id=current.trace_id,
                span_id=new_span_id(),
                payload={
                    "status": status.value,
                    "summary": summary or {},
                },
            )
        )
        return completed

    def get_run(self, run_id: UUID) -> RunRecord:
        return self._runs[run_id]

    def bind_lineage(
        self,
        run_id: UUID,
        *,
        lineage_graph_digest: str,
        specification_revision: LineageNodeRef | None = None,
    ) -> RunRecord:
        current = self._runs[run_id]
        if (
            current.specification_revision is not None
            and specification_revision is not None
            and current.specification_revision != specification_revision
        ):
            raise ValueError("a run cannot be rebound to another specification revision")
        updated = current.model_copy(
            update={
                "lineage_graph_digest": lineage_graph_digest,
                "specification_revision": (
                    specification_revision or current.specification_revision
                ),
            }
        )
        self._runs[run_id] = updated
        return updated
