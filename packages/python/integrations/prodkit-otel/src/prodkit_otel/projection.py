from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from prodkit_control_core import ControlEvent, EventType


class OpenTelemetryEventProjector:
    """Project canonical evidence to telemetry without making telemetry authoritative.

    The projector intentionally emits bounded metadata rather than arbitrary event payloads. The
    canonical ledger remains the source of truth; OpenTelemetry is an operational projection that
    may be sampled, dropped, or retained under a different policy.
    """

    def __init__(self, instrumentation_name: str = "prodkit.control") -> None:
        self._tracer = trace.get_tracer(instrumentation_name)

    @staticmethod
    def attributes(event: ControlEvent) -> dict[str, str | int | bool]:
        attributes: dict[str, str | int | bool] = {
            "prodkit.run.id": str(event.run_id),
            "prodkit.event.id": str(event.event_id),
            "prodkit.event.type": event.event_type.value,
            "prodkit.event.sequence": event.sequence,
            "prodkit.event.hash": event.integrity.event_hash,
            "prodkit.event.schema_version": event.schema_version,
            "prodkit.tenant.id": event.tenant_id,
            "prodkit.trace.id": event.trace_id,
            "prodkit.span.id": event.span_id,
        }
        if event.action_id is not None:
            attributes["prodkit.action.id"] = str(event.action_id)
        if event.parent_event_id is not None:
            attributes["prodkit.event.parent_id"] = str(event.parent_event_id)
        if event.causation_event_id is not None:
            attributes["prodkit.event.causation_id"] = str(event.causation_event_id)
        if event.correlation_id is not None:
            attributes["prodkit.correlation.id"] = event.correlation_id
        if event.lineage:
            attributes["prodkit.lineage.reference_count"] = len(event.lineage)
        if event.evidence:
            attributes["prodkit.evidence.reference_count"] = len(event.evidence)
        return attributes

    @staticmethod
    def error_type(event: ControlEvent) -> str | None:
        if event.event_type is EventType.EXECUTION_UNCERTAIN:
            return "execution_uncertain"
        if event.event_type is EventType.CREDENTIAL_LEASE_REVOCATION_FAILED:
            return "credential_lease_revocation_failed"
        if event.event_type is EventType.EXECUTION_COMPLETED:
            result = event.payload.get("result")
            if isinstance(result, dict) and result.get("succeeded") is False:
                raw = result.get("error_type")
                return str(raw) if raw else "execution_failed"
        if event.event_type is EventType.VERIFICATION_COMPLETED:
            verification = event.payload.get("verification")
            if isinstance(verification, dict) and verification.get("outcome") == "failed":
                return "verification_failed"
        return None

    def project(self, event: ControlEvent) -> None:
        timestamp_ns = int(event.occurred_at.timestamp() * 1_000_000_000)
        attributes = self.attributes(event)
        error_type = self.error_type(event)
        if error_type is not None:
            attributes["error.type"] = error_type

        span = self._tracer.start_span(
            event.event_type.value,
            kind=SpanKind.INTERNAL,
            attributes=attributes,
            start_time=timestamp_ns,
        )
        try:
            if error_type is not None:
                span.set_status(Status(StatusCode.ERROR))
        finally:
            span.end(end_time=timestamp_ns)
