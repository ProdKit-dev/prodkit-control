from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from prodkit_control_core import EventType, ControlEvent


@dataclass(frozen=True)
class RunProjection:
    run_id: UUID
    event_count: int
    final_event_hash: str | None
    counts_by_type: dict[str, int]
    action_ids: tuple[UUID, ...]
    completed: bool


def project_run(events: list[ControlEvent]) -> RunProjection:
    if not events:
        raise ValueError("cannot project an empty run")
    run_id = events[0].run_id
    if any(event.run_id != run_id for event in events):
        raise ValueError("all events must belong to the same run")
    counts = Counter(event.event_type.value for event in events)
    action_ids = tuple(dict.fromkeys(event.action_id for event in events if event.action_id))
    return RunProjection(
        run_id=run_id,
        event_count=len(events),
        final_event_hash=events[-1].integrity.event_hash,
        counts_by_type=dict(sorted(counts.items())),
        action_ids=action_ids,
        completed=counts[EventType.RUN_COMPLETED.value] > 0,
    )
