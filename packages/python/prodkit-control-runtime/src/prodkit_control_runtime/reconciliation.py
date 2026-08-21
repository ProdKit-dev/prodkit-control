from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from prodkit_control_core import (
    ExpectedExternalAction,
    ExternalAuditEvent,
    ProductionCompletenessAssessment,
    ProductionCompletenessProfile,
    ReconciliationBatch,
    ReconciliationCursor,
    ReconciliationFinding,
    ReconciliationRunResult,
    ReconciliationSourceConfig,
    ReconciliationSourceHealth,
)
from prodkit_control_core.reconciliation import ReconciliationEngine, assess_production_completeness


class ReconciliationSource(Protocol):
    source_system: str

    async def collect(
        self,
        *,
        tenant_id: str,
        cursor: ReconciliationCursor | None,
        collected_at: datetime,
    ) -> ReconciliationBatch: ...


class ReconciliationStore(Protocol):
    async def get_cursor(
        self, tenant_id: str, source_system: str
    ) -> ReconciliationCursor | None: ...

    async def save_cursor(self, cursor: ReconciliationCursor) -> None: ...

    async def save_result(self, result: ReconciliationRunResult) -> None: ...

    async def ingest_audit_event(self, event: ExternalAuditEvent) -> bool: ...

    async def list_findings(self, tenant_id: str) -> tuple[ReconciliationFinding, ...]: ...


class InMemoryReconciliationStore:
    def __init__(self) -> None:
        self._cursors: dict[tuple[str, str], ReconciliationCursor] = {}
        self._results: dict[UUID, ReconciliationRunResult] = {}
        self._findings: dict[UUID, ReconciliationFinding] = {}
        self._finding_tenants: dict[UUID, str] = {}
        self._audit_events: set[tuple[str, str, str]] = set()

    async def get_cursor(self, tenant_id: str, source_system: str) -> ReconciliationCursor | None:
        return self._cursors.get((tenant_id, source_system))

    async def save_cursor(self, cursor: ReconciliationCursor) -> None:
        self._cursors[(cursor.tenant_id, cursor.source_system)] = cursor

    async def save_result(self, result: ReconciliationRunResult) -> None:
        existing = self._results.get(result.reconciliation_id)
        if existing is not None and existing != result:
            raise ValueError("reconciliation result identity cannot be rewritten")
        self._results[result.reconciliation_id] = result
        for finding in result.findings:
            prior = self._findings.get(finding.finding_id)
            if prior is not None and prior != finding:
                raise ValueError("reconciliation finding identity cannot be rewritten")
            self._findings[finding.finding_id] = finding
            self._finding_tenants[finding.finding_id] = result.tenant_id

    async def ingest_audit_event(self, event: ExternalAuditEvent) -> bool:
        key = (event.tenant_id, event.source_system, event.event_id)
        if key in self._audit_events:
            return False
        self._audit_events.add(key)
        return True

    async def list_findings(self, tenant_id: str) -> tuple[ReconciliationFinding, ...]:
        return tuple(
            sorted(
                (
                    item
                    for key, item in self._findings.items()
                    if self._finding_tenants[key] == tenant_id
                ),
                key=lambda item: (item.observed_at, str(item.finding_id)),
            )
        )


class ReconciliationCoordinator:
    """Runs incremental sources without ever translating unknown source state into success."""

    def __init__(
        self,
        *,
        store: ReconciliationStore,
        engine: ReconciliationEngine | None = None,
    ) -> None:
        self._store = store
        self._engine = engine or ReconciliationEngine()

    async def run_source(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        source: ReconciliationSource,
        config: ReconciliationSourceConfig,
        expected_actions: Sequence[ExpectedExternalAction],
        now: datetime | None = None,
    ) -> ReconciliationRunResult | None:
        current_time = now or datetime.now(UTC)
        if not config.enabled:
            return None
        if source.source_system != config.source_system:
            raise ValueError("source implementation and configuration names differ")

        cursor = await self._store.get_cursor(tenant_id, source.source_system)
        if (
            cursor is not None
            and cursor.next_attempt_at is not None
            and cursor.next_attempt_at > current_time
        ):
            return None

        started_at = current_time
        failures = cursor.consecutive_failures if cursor is not None else 0
        try:
            batch = await source.collect(
                tenant_id=tenant_id,
                cursor=cursor,
                collected_at=current_time,
            )
        except Exception as exc:
            failures += 1
            batch = ReconciliationBatch(
                tenant_id=tenant_id,
                source_system=source.source_system,
                collected_at=current_time,
                health=ReconciliationSourceHealth.UNAVAILABLE,
            )
            failure_cursor = ReconciliationCursor(
                tenant_id=tenant_id,
                source_system=source.source_system,
                cursor=cursor.cursor if cursor is not None else None,
                high_watermark=cursor.high_watermark if cursor is not None else None,
                health=ReconciliationSourceHealth.UNAVAILABLE,
                consecutive_failures=failures,
                next_attempt_at=current_time + config.backoff_for(failures),
                updated_at=current_time,
            )
            await self._store.save_cursor(failure_cursor)
            findings = self._engine.reconcile(
                run_id=run_id,
                expected_actions=tuple(expected_actions),
                batch=batch,
            )
            findings = tuple(
                finding.model_copy(
                    update={
                        "details": {
                            **finding.details,
                            "collection_error_type": type(exc).__name__,
                        }
                    }
                )
                for finding in findings
            )
            result = ReconciliationRunResult(
                reconciliation_id=uuid4(),
                run_id=run_id,
                tenant_id=tenant_id,
                source_system=source.source_system,
                started_at=started_at,
                completed_at=current_time,
                health=ReconciliationSourceHealth.UNAVAILABLE,
                cursor=failure_cursor.cursor,
                high_watermark=failure_cursor.high_watermark,
                findings=findings,
            )
            await self._store.save_result(result)
            return result

        for event in batch.audit_events:
            await self._store.ingest_audit_event(event)

        effective_health = batch.health
        if (
            effective_health is ReconciliationSourceHealth.HEALTHY
            and batch.high_watermark is not None
            and (current_time - batch.high_watermark).total_seconds() > config.stale_after_seconds
        ):
            effective_health = ReconciliationSourceHealth.STALE
            batch = batch.model_copy(update={"health": effective_health})

        findings = self._engine.reconcile(
            run_id=run_id,
            expected_actions=tuple(expected_actions),
            batch=batch,
        )
        next_cursor = ReconciliationCursor(
            tenant_id=tenant_id,
            source_system=source.source_system,
            cursor=batch.cursor,
            high_watermark=batch.high_watermark,
            health=effective_health,
            consecutive_failures=0,
            next_attempt_at=current_time + config.backoff_for(0),
            updated_at=current_time,
        )
        result = ReconciliationRunResult(
            reconciliation_id=uuid4(),
            run_id=run_id,
            tenant_id=tenant_id,
            source_system=source.source_system,
            started_at=started_at,
            completed_at=current_time,
            health=effective_health,
            cursor=batch.cursor,
            high_watermark=batch.high_watermark,
            findings=findings,
        )
        await self._store.save_result(result)
        await self._store.save_cursor(next_cursor)
        return result

    async def assess_completeness(
        self,
        *,
        profile: ProductionCompletenessProfile,
        now: datetime | None = None,
    ) -> ProductionCompletenessAssessment:
        assessed_at = now or datetime.now(UTC)
        health: dict[str, tuple[ReconciliationSourceHealth, datetime | None]] = {}
        for source in profile.required_sources:
            cursor = await self._store.get_cursor(profile.tenant_id, source)
            if cursor is None:
                health[source] = (ReconciliationSourceHealth.UNAVAILABLE, None)
            else:
                health[source] = (cursor.health, cursor.high_watermark)
        findings = await self._store.list_findings(profile.tenant_id)
        return assess_production_completeness(
            profile=profile,
            assessed_at=assessed_at,
            source_health=health,
            findings=findings,
        )
