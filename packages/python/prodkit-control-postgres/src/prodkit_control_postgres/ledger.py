from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    EventIntegrity,
    ControlEvent,
    ControlEventDraft,
    IntegrityViolationError,
    sha256_hex,
)

from .models import ControlEventRow


class PostgresEventLedger:
    """Transactional append-only ledger using a per-run PostgreSQL advisory lock."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def append(self, draft: ControlEventDraft) -> ControlEvent:
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:run_id, 0))"),
                {"run_id": str(draft.run_id)},
            )
            last = await session.scalar(
                select(ControlEventRow)
                .where(ControlEventRow.run_id == draft.run_id)
                .order_by(ControlEventRow.sequence.desc())
                .limit(1)
            )
            sequence = (last.sequence + 1) if last else 1
            previous = last.event_hash if last else None
            material = {**draft.model_dump(mode="python"), "sequence": sequence}
            event_hash = sha256_hex({"event": material, "previous_event_hash": previous})
            event = ControlEvent(
                **draft.model_dump(mode="python"),
                sequence=sequence,
                integrity=EventIntegrity(previous_event_hash=previous, event_hash=event_hash),
            )
            session.add(
                ControlEventRow(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    action_id=event.action_id,
                    tenant_id=event.tenant_id,
                    sequence=event.sequence,
                    event_type=event.event_type.value,
                    recorded_at=event.recorded_at,
                    previous_event_hash=previous,
                    event_hash=event_hash,
                    document=event.model_dump(mode="json"),
                )
            )
            return event

    async def list_run_events(self, run_id: UUID) -> list[ControlEvent]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ControlEventRow)
                    .where(ControlEventRow.run_id == run_id)
                    .order_by(ControlEventRow.sequence)
                )
            ).all()
        return [ControlEvent.model_validate(row.document) for row in rows]

    async def stream_run_events(self, run_id: UUID) -> AsyncIterator[ControlEvent]:
        for event in await self.list_run_events(run_id):
            yield event

    async def verify_run(self, run_id: UUID) -> None:
        previous = None
        for expected_sequence, event in enumerate(await self.list_run_events(run_id), start=1):
            if event.sequence != expected_sequence:
                raise IntegrityViolationError("run event sequence is not contiguous")
            if event.integrity.previous_event_hash != previous:
                raise IntegrityViolationError("run event previous hash is invalid")
            expected_hash = sha256_hex(
                {"event": event.hash_material(), "previous_event_hash": previous}
            )
            if event.integrity.event_hash != expected_hash:
                raise IntegrityViolationError("run event hash is invalid")
            previous = event.integrity.event_hash
