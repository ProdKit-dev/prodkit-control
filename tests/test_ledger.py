from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    EventType,
    ControlEventDraft,
    IntegrityViolationError,
)
from prodkit_control_runtime import InMemoryEventLedger


@pytest.mark.asyncio
async def test_ledger_sequences_and_verifies(tenant_id: str) -> None:
    ledger = InMemoryEventLedger()
    run_id = uuid4()
    actor = ActorRef(kind=ActorKind.SERVICE, id="service", tenant_id=tenant_id)
    for event_type in (EventType.RUN_STARTED, EventType.RUN_COMPLETED):
        now = datetime.now(UTC)
        await ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=event_type,
                occurred_at=now,
                recorded_at=now,
                actor=actor,
                trace_id="a" * 32,
                span_id="b" * 16,
            )
        )
    events = await ledger.list_run_events(tenant_id=tenant_id, run_id=run_id)
    assert [event.sequence for event in events] == [1, 2]
    assert events[1].integrity.previous_event_hash == events[0].integrity.event_hash
    assert await ledger.list_run_events(tenant_id="foreign-tenant", run_id=run_id) == []
    await ledger.verify_run(tenant_id=tenant_id, run_id=run_id)


@pytest.mark.asyncio
async def test_ledger_detects_tampering(tenant_id: str) -> None:
    ledger = InMemoryEventLedger()
    run_id = uuid4()
    now = datetime.now(UTC)
    event = await ledger.append(
        ControlEventDraft(
            event_id=uuid4(),
            run_id=run_id,
            tenant_id=tenant_id,
            event_type=EventType.RUN_STARTED,
            occurred_at=now,
            recorded_at=now,
            actor=ActorRef(kind=ActorKind.SERVICE, id="service", tenant_id=tenant_id),
            trace_id="a" * 32,
            span_id="b" * 16,
        )
    )
    tampered = event.model_copy(update={"payload": {"modified": True}})
    ledger.replace_for_test(tenant_id=tenant_id, run_id=run_id, events=[tampered])
    with pytest.raises(IntegrityViolationError):
        await ledger.verify_run(tenant_id=tenant_id, run_id=run_id)
