from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from prodkit_control_core.canonical import canonical_json_bytes, sha256_hex
from prodkit_control_core.contracts.reconciliation import (
    ExternalAuditEvent,
    ExternalStateObservation,
    ReconciliationBatch,
    ReconciliationSourceHealth,
)


def _uuid(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    return UUID(str(value))


def _timestamp(value: object, *, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class MappingReconciler:
    """Normalize provider records into canonical reconciliation evidence.

    Provider adapters intentionally remain pure: fetching/authentication belongs to the host
    integration, while this class owns stable interpretation of returned provider records.
    """

    def __init__(self, source_system: str) -> None:
        self.source_system = source_system

    def normalize(
        self,
        *,
        tenant_id: str,
        records: list[dict[str, Any]],
        collected_at: datetime,
        cursor: str | None = None,
        health: ReconciliationSourceHealth = ReconciliationSourceHealth.HEALTHY,
    ) -> ReconciliationBatch:
        observations: list[ExternalStateObservation] = []
        events: list[ExternalAuditEvent] = []
        high_watermark: datetime | None = None

        for index, record in enumerate(records):
            record_type = str(record.get("record_type", "state"))
            observed_at = _timestamp(
                record.get("observed_at") or record.get("occurred_at"),
                fallback=collected_at,
            )
            high_watermark = (
                observed_at
                if high_watermark is None or observed_at > high_watermark
                else high_watermark
            )
            external_reference = (
                str(record["external_reference"])
                if record.get("external_reference") is not None
                else None
            )
            if record_type == "audit":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                payload_digest = record.get("payload_digest")
                if payload_digest is None:
                    payload_digest = sha256_hex(canonical_json_bytes(payload))
                events.append(
                    ExternalAuditEvent(
                        event_id=str(record.get("event_id") or f"audit-{index}"),
                        tenant_id=tenant_id,
                        source_system=self.source_system,
                        event_type=str(record.get("event_type") or "external.action"),
                        occurred_at=observed_at,
                        payload_digest=str(payload_digest),
                        actor=str(record["actor"]) if record.get("actor") is not None else None,
                        resource=(
                            str(record["resource"]) if record.get("resource") is not None else None
                        ),
                        action_id=_uuid(record.get("action_id")),
                        run_id=_uuid(record.get("run_id")),
                        external_reference=external_reference,
                        payload=payload,
                    )
                )
                continue

            state = record.get("state")
            if not isinstance(state, dict):
                state = {}
            state_digest = record.get("state_digest")
            if state_digest is None:
                state_digest = sha256_hex(canonical_json_bytes(state))
            observations.append(
                ExternalStateObservation(
                    observation_id=str(record.get("observation_id") or f"state-{index}"),
                    tenant_id=tenant_id,
                    source_system=self.source_system,
                    observed_at=observed_at,
                    state_digest=str(state_digest),
                    action_id=_uuid(record.get("action_id")),
                    run_id=_uuid(record.get("run_id")),
                    external_reference=external_reference,
                    state=state,
                )
            )

        return ReconciliationBatch(
            tenant_id=tenant_id,
            source_system=self.source_system,
            collected_at=collected_at,
            health=health,
            cursor=cursor,
            high_watermark=high_watermark,
            observations=tuple(observations),
            audit_events=tuple(events),
        )
