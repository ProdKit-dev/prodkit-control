from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    ExternalAuditEvent,
    ProductionCompletenessProfile,
    ReconciliationCursor,
    ReconciliationFinding,
    ReconciliationRunResult,
)


class PostgresReconciliationStore:
    """Durable, tenant-scoped reconciliation state with idempotent evidence ingestion."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_cursor(self, tenant_id: str, source_system: str) -> ReconciliationCursor | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT document
                        FROM reconciliation_cursors
                        WHERE tenant_id = :tenant_id AND source_system = :source_system
                        """
                    ),
                    {"tenant_id": tenant_id, "source_system": source_system},
                )
            ).mappings().first()
            return ReconciliationCursor.model_validate(row["document"]) if row is not None else None

    async def save_cursor(self, cursor: ReconciliationCursor) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO reconciliation_cursors (
                      tenant_id, source_system, cursor, high_watermark, health,
                      consecutive_failures, next_attempt_at, updated_at, document
                    ) VALUES (
                      :tenant_id, :source_system, :cursor, :high_watermark, :health,
                      :consecutive_failures, :next_attempt_at, :updated_at, CAST(:document AS JSONB)
                    )
                    ON CONFLICT (tenant_id, source_system) DO UPDATE SET
                      cursor = EXCLUDED.cursor,
                      high_watermark = EXCLUDED.high_watermark,
                      health = EXCLUDED.health,
                      consecutive_failures = EXCLUDED.consecutive_failures,
                      next_attempt_at = EXCLUDED.next_attempt_at,
                      updated_at = EXCLUDED.updated_at,
                      document = EXCLUDED.document
                    """
                ),
                {
                    "tenant_id": cursor.tenant_id,
                    "source_system": cursor.source_system,
                    "cursor": cursor.cursor,
                    "high_watermark": cursor.high_watermark,
                    "health": cursor.health.value,
                    "consecutive_failures": cursor.consecutive_failures,
                    "next_attempt_at": cursor.next_attempt_at,
                    "updated_at": cursor.updated_at,
                    "document": cursor.model_dump_json(),
                },
            )

    async def save_result(self, result: ReconciliationRunResult) -> None:
        async with self._sessions.begin() as session:
            inserted = await session.execute(
                text(
                    """
                    INSERT INTO reconciliation_results (
                      reconciliation_id, run_id, tenant_id, source_system,
                      started_at, completed_at, health, document
                    ) VALUES (
                      :reconciliation_id, :run_id, :tenant_id, :source_system,
                      :started_at, :completed_at, :health, CAST(:document AS JSONB)
                    )
                    ON CONFLICT (reconciliation_id) DO NOTHING
                    RETURNING reconciliation_id
                    """
                ),
                {
                    "reconciliation_id": result.reconciliation_id,
                    "run_id": result.run_id,
                    "tenant_id": result.tenant_id,
                    "source_system": result.source_system,
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                    "health": result.health.value,
                    "document": result.model_dump_json(),
                },
            )
            if inserted.scalar_one_or_none() is None:
                existing = (
                    await session.execute(
                        text(
                            """
                            SELECT document FROM reconciliation_results
                            WHERE reconciliation_id = :reconciliation_id
                            """
                        ),
                        {"reconciliation_id": result.reconciliation_id},
                    )
                ).mappings().one()
                if ReconciliationRunResult.model_validate(existing["document"]) != result:
                    raise ValueError("reconciliation result identity cannot be rewritten")
                return

            for finding in result.findings:
                await session.execute(
                    text(
                        """
                        INSERT INTO reconciliation_findings (
                          finding_id, reconciliation_id, run_id, action_id, tenant_id,
                          source_system, outcome, severity, observed_at, document
                        ) VALUES (
                          :finding_id, :reconciliation_id, :run_id, :action_id, :tenant_id,
                          :source_system, :outcome, :severity, :observed_at, CAST(:document AS JSONB)
                        )
                        ON CONFLICT (finding_id) DO NOTHING
                        """
                    ),
                    {
                        "finding_id": finding.finding_id,
                        "reconciliation_id": result.reconciliation_id,
                        "run_id": finding.run_id,
                        "action_id": finding.action_id,
                        "tenant_id": result.tenant_id,
                        "source_system": finding.source_system,
                        "outcome": finding.outcome.value,
                        "severity": finding.severity,
                        "observed_at": finding.observed_at,
                        "document": finding.model_dump_json(),
                    },
                )

    async def ingest_audit_event(self, event: ExternalAuditEvent) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(
                text(
                    """
                    INSERT INTO external_audit_events (
                      tenant_id, source_system, event_id, occurred_at, payload_digest, document
                    ) VALUES (
                      :tenant_id, :source_system, :event_id, :occurred_at,
                      :payload_digest, CAST(:document AS JSONB)
                    )
                    ON CONFLICT (tenant_id, source_system, event_id) DO NOTHING
                    RETURNING event_id
                    """
                ),
                {
                    "tenant_id": event.tenant_id,
                    "source_system": event.source_system,
                    "event_id": event.event_id,
                    "occurred_at": event.occurred_at,
                    "payload_digest": event.payload_digest,
                    "document": event.model_dump_json(),
                },
            )
            return result.scalar_one_or_none() is not None

    async def list_findings(self, tenant_id: str) -> tuple[ReconciliationFinding, ...]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT document
                        FROM reconciliation_findings
                        WHERE tenant_id = :tenant_id
                        ORDER BY observed_at, finding_id
                        """
                    ),
                    {"tenant_id": tenant_id},
                )
            ).mappings().all()
        return tuple(ReconciliationFinding.model_validate(row["document"]) for row in rows)

    async def save_profile(self, profile: ProductionCompletenessProfile) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO production_completeness_profiles (
                      tenant_id, profile_id, updated_at, document
                    ) VALUES (
                      :tenant_id, :profile_id, :updated_at, CAST(:document AS JSONB)
                    )
                    ON CONFLICT (tenant_id, profile_id) DO UPDATE SET
                      updated_at = EXCLUDED.updated_at,
                      document = EXCLUDED.document
                    """
                ),
                {
                    "tenant_id": profile.tenant_id,
                    "profile_id": profile.profile_id,
                    "updated_at": now,
                    "document": profile.model_dump_json(),
                },
            )

    async def get_profile(
        self, tenant_id: str, profile_id: str
    ) -> ProductionCompletenessProfile | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT document
                        FROM production_completeness_profiles
                        WHERE tenant_id = :tenant_id AND profile_id = :profile_id
                        """
                    ),
                    {"tenant_id": tenant_id, "profile_id": profile_id},
                )
            ).mappings().first()
        return ProductionCompletenessProfile.model_validate(row["document"]) if row is not None else None
