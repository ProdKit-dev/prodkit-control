from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from prodkit_control_core import ControlEvent


class OpenTelemetryEventProjector:
    """Project canonical events to operational spans without making traces authoritative."""

    def __init__(self, instrumentation_name: str = "prodkit.control") -> None:
        self._tracer = trace.get_tracer(instrumentation_name)

    def project(self, event: ControlEvent) -> None:
        with self._tracer.start_as_current_span(
            event.event_type.value,
            kind=SpanKind.INTERNAL,
            attributes={
                "prodkit.run.id": str(event.run_id),
                "prodkit.event.id": str(event.event_id),
                "prodkit.event.sequence": event.sequence,
                "prodkit.event.hash": event.integrity.event_hash,
                "prodkit.tenant.id": event.tenant_id,
                "prodkit.action.id": str(event.action_id) if event.action_id else "",
            },
        ):
            pass
