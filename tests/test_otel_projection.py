from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from prodkit_control_core import ActorKind, ActorRef, ControlEvent, EventIntegrity, EventType
from prodkit_otel import OpenTelemetryEventProjector


def _event(*, event_type: EventType, payload: dict[str, object]) -> ControlEvent:
    now = datetime.now(UTC)
    return ControlEvent(
        event_id=uuid4(),
        run_id=uuid4(),
        tenant_id="tenant-a",
        event_type=event_type,
        occurred_at=now,
        recorded_at=now,
        actor=ActorRef(
            kind=ActorKind.SERVICE,
            id="control-service",
            tenant_id="tenant-a",
        ),
        trace_id="a" * 32,
        span_id="b" * 16,
        action_id=uuid4(),
        payload=payload,
        sequence=7,
        integrity=EventIntegrity(event_hash="c" * 64),
    )


def test_otel_projection_attributes_are_bounded_and_exclude_payload() -> None:
    event = _event(
        event_type=EventType.EXECUTION_COMPLETED,
        payload={
            "result": {"succeeded": True},
            "secret": "must-never-become-a-span-attribute",
        },
    )
    attributes = OpenTelemetryEventProjector.attributes(event)
    assert attributes["prodkit.event.type"] == "execution.completed"
    assert attributes["prodkit.event.sequence"] == 7
    assert attributes["prodkit.event.hash"] == "c" * 64
    assert "secret" not in attributes
    assert all("must-never" not in str(value) for value in attributes.values())


def test_otel_projection_uses_predictable_error_type() -> None:
    failed = _event(
        event_type=EventType.EXECUTION_COMPLETED,
        payload={
            "result": {
                "succeeded": False,
                "error_type": "TimeoutError",
                "error_message": "provider diagnostic is intentionally not projected",
            }
        },
    )
    uncertain = _event(event_type=EventType.EXECUTION_UNCERTAIN, payload={})
    assert OpenTelemetryEventProjector.error_type(failed) == "TimeoutError"
    assert OpenTelemetryEventProjector.error_type(uncertain) == "execution_uncertain"
