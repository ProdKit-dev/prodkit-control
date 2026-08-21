from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from prodkit_control_core import (
    ExpectedExternalAction,
    ExternalAuditEvent,
    ExternalStateObservation,
    ProductionCompletenessProfile,
    ReconciliationBatch,
    ReconciliationOutcome,
    ReconciliationSourceConfig,
    ReconciliationSourceHealth,
)
from prodkit_control_core.reconciliation import ReconciliationEngine, assess_production_completeness
from prodkit_control_runtime import InMemoryReconciliationStore, ReconciliationCoordinator
from prodkit_reconcile_ci import CIReconciler
from prodkit_reconcile_database import DatabaseReconciler
from prodkit_reconcile_deployment import DeploymentReconciler
from prodkit_reconcile_git import GitReconciler
from prodkit_reconcile_github import GitHubReconciler
from prodkit_reconcile_kubernetes import KubernetesReconciler
from prodkit_reconcile_registry import RegistryReconciler


def expected(*, tenant_id: str, run_id, source: str, digest: str = "a" * 64):
    return ExpectedExternalAction(
        action_id=uuid4(),
        run_id=run_id,
        tenant_id=tenant_id,
        source_system=source,
        expected_state_digest=digest,
    )


def observation(*, tenant_id: str, action, now: datetime, digest: str = "a" * 64):
    return ExternalStateObservation(
        observation_id=str(uuid4()),
        tenant_id=tenant_id,
        source_system=action.source_system,
        observed_at=now,
        state_digest=digest,
        action_id=action.action_id,
        run_id=action.run_id,
    )


def test_reconciliation_detects_match_missing_mismatch_and_bypass(tenant_id, run_id):
    now = datetime.now(UTC)
    engine = ReconciliationEngine()
    matched = expected(tenant_id=tenant_id, run_id=run_id, source="github")
    missing = expected(tenant_id=tenant_id, run_id=run_id, source="github")
    mismatched = expected(tenant_id=tenant_id, run_id=run_id, source="github")
    bypass = ExternalStateObservation(
        observation_id="bypass",
        tenant_id=tenant_id,
        source_system="github",
        observed_at=now,
        state_digest="c" * 64,
    )
    batch = ReconciliationBatch(
        tenant_id=tenant_id,
        source_system="github",
        collected_at=now,
        high_watermark=now,
        health=ReconciliationSourceHealth.HEALTHY,
        observations=(
            observation(tenant_id=tenant_id, action=matched, now=now),
            observation(tenant_id=tenant_id, action=mismatched, now=now, digest="b" * 64),
            bypass,
        ),
    )

    findings = engine.reconcile(
        run_id=run_id,
        expected_actions=(matched, missing, mismatched),
        batch=batch,
    )

    outcomes = {item.outcome for item in findings}
    assert ReconciliationOutcome.MATCHED in outcomes
    assert ReconciliationOutcome.MISSING_EXTERNAL_EVIDENCE in outcomes
    assert ReconciliationOutcome.STATE_MISMATCH in outcomes
    assert ReconciliationOutcome.UNEXPECTED_EXTERNAL_ACTION in outcomes
    bypass_finding = next(
        item
        for item in findings
        if item.outcome is ReconciliationOutcome.UNEXPECTED_EXTERNAL_ACTION
    )
    assert bypass_finding.severity == "high"


def test_conflicting_and_stale_evidence_never_passes(tenant_id, run_id):
    now = datetime.now(UTC)
    action = expected(tenant_id=tenant_id, run_id=run_id, source="kubernetes")
    engine = ReconciliationEngine()
    conflicting = ReconciliationBatch(
        tenant_id=tenant_id,
        source_system="kubernetes",
        collected_at=now,
        health=ReconciliationSourceHealth.HEALTHY,
        observations=(
            observation(tenant_id=tenant_id, action=action, now=now, digest="a" * 64),
            observation(tenant_id=tenant_id, action=action, now=now, digest="b" * 64),
        ),
    )
    findings = engine.reconcile(run_id=run_id, expected_actions=(action,), batch=conflicting)
    assert {item.outcome for item in findings} == {ReconciliationOutcome.CONFLICTING_EVIDENCE}

    stale = conflicting.model_copy(
        update={
            "health": ReconciliationSourceHealth.STALE,
            "observations": (observation(tenant_id=tenant_id, action=action, now=now),),
        }
    )
    stale_findings = engine.reconcile(run_id=run_id, expected_actions=(action,), batch=stale)
    assert ReconciliationOutcome.UNVERIFIABLE in {item.outcome for item in stale_findings}
    assert ReconciliationOutcome.MATCHED not in {item.outcome for item in stale_findings}


@pytest.mark.asyncio
async def test_coordinator_persists_cursor_backoff_and_unavailable_findings(tenant_id, run_id):
    now = datetime.now(UTC)
    action = expected(tenant_id=tenant_id, run_id=run_id, source="database")

    class BrokenSource:
        source_system = "database"

        async def collect(self, **kwargs):
            raise TimeoutError("provider did not respond")

    store = InMemoryReconciliationStore()
    coordinator = ReconciliationCoordinator(store=store)
    config = ReconciliationSourceConfig(
        source_system="database",
        base_backoff_seconds=10,
        max_backoff_seconds=60,
    )
    result = await coordinator.run_source(
        run_id=run_id,
        tenant_id=tenant_id,
        source=BrokenSource(),
        config=config,
        expected_actions=(action,),
        now=now,
    )
    assert result is not None
    assert result.health is ReconciliationSourceHealth.UNAVAILABLE
    assert all(item.outcome is ReconciliationOutcome.UNVERIFIABLE for item in result.findings)
    cursor = await store.get_cursor(tenant_id, "database")
    assert cursor is not None
    assert cursor.consecutive_failures == 1
    assert cursor.next_attempt_at == now + timedelta(seconds=10)

    assert (
        await coordinator.run_source(
            run_id=run_id,
            tenant_id=tenant_id,
            source=BrokenSource(),
            config=config,
            expected_actions=(action,),
            now=now + timedelta(seconds=5),
        )
        is None
    )


def test_completeness_requires_fresh_healthy_matched_sources(tenant_id, run_id):
    now = datetime.now(UTC)
    profile = ProductionCompletenessProfile(
        profile_id="production",
        tenant_id=tenant_id,
        required_sources=("github", "registry"),
        max_source_age_seconds=300,
    )
    github_action = expected(tenant_id=tenant_id, run_id=run_id, source="github")
    registry_action = expected(tenant_id=tenant_id, run_id=run_id, source="registry")
    engine = ReconciliationEngine()
    findings = []
    for action in (github_action, registry_action):
        batch = ReconciliationBatch(
            tenant_id=tenant_id,
            source_system=action.source_system,
            collected_at=now,
            high_watermark=now,
            health=ReconciliationSourceHealth.HEALTHY,
            observations=(observation(tenant_id=tenant_id, action=action, now=now),),
        )
        findings.extend(engine.reconcile(run_id=run_id, expected_actions=(action,), batch=batch))
    assessment = assess_production_completeness(
        profile=profile,
        assessed_at=now,
        source_health={
            "github": (ReconciliationSourceHealth.HEALTHY, now),
            "registry": (ReconciliationSourceHealth.HEALTHY, now),
        },
        findings=tuple(findings),
    )
    assert assessment.complete

    stale = assess_production_completeness(
        profile=profile,
        assessed_at=now,
        source_health={
            "github": (ReconciliationSourceHealth.HEALTHY, now - timedelta(hours=1)),
            "registry": (ReconciliationSourceHealth.HEALTHY, now),
        },
        findings=tuple(findings),
    )
    assert not stale.complete
    assert stale.stale_sources == ("github",)


def test_provider_reconcilers_emit_canonical_scoped_batches(tenant_id):
    now = datetime.now(UTC)
    action_id = uuid4()
    adapters = (
        GitReconciler(),
        GitHubReconciler(),
        CIReconciler(),
        RegistryReconciler(),
        DeploymentReconciler(),
        KubernetesReconciler(),
        DatabaseReconciler(),
    )
    for adapter in adapters:
        batch = adapter.normalize(
            tenant_id=tenant_id,
            collected_at=now,
            cursor="cursor-2",
            records=[
                {
                    "record_type": "state",
                    "observation_id": "state-1",
                    "observed_at": now.isoformat(),
                    "action_id": str(action_id),
                    "state": {"status": "ok"},
                },
                {
                    "record_type": "audit",
                    "event_id": "event-1",
                    "occurred_at": now.isoformat(),
                    "event_type": "provider.write",
                    "payload": {"resource": "example"},
                },
            ],
        )
        assert batch.source_system == adapter.source_system
        assert batch.tenant_id == tenant_id
        assert batch.cursor == "cursor-2"
        assert len(batch.observations) == 1
        assert batch.observations[0].action_id == action_id
        assert len(batch.audit_events) == 1


@pytest.mark.asyncio
async def test_external_audit_ingestion_is_idempotent(tenant_id):
    now = datetime.now(UTC)
    event = ExternalAuditEvent(
        event_id="evt-1",
        tenant_id=tenant_id,
        source_system="github",
        event_type="repo.push",
        occurred_at=now,
        payload_digest="a" * 64,
    )
    store = InMemoryReconciliationStore()
    assert await store.ingest_audit_event(event)
    assert not await store.ingest_audit_event(event)


def test_backoff_is_exponential_and_capped():
    config = ReconciliationSourceConfig(
        source_system="github",
        poll_interval_seconds=300,
        base_backoff_seconds=10,
        max_backoff_seconds=40,
    )
    assert config.backoff_for(0) == timedelta(seconds=300)
    assert config.backoff_for(1) == timedelta(seconds=10)
    assert config.backoff_for(2) == timedelta(seconds=20)
    assert config.backoff_for(3) == timedelta(seconds=40)
    assert config.backoff_for(20) == timedelta(seconds=40)


def test_sanitized_provider_contract_fixtures_cover_required_sources(tenant_id):
    fixture_path = Path(__file__).parent / "fixtures/reconciliation/provider_records.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    adapters = {
        "git": GitReconciler(),
        "github": GitHubReconciler(),
        "ci": CIReconciler(),
        "registry": RegistryReconciler(),
        "deployment": DeploymentReconciler(),
        "kubernetes": KubernetesReconciler(),
        "database": DatabaseReconciler(),
    }
    assert set(payload) == set(adapters)
    collected_at = datetime(2026, 8, 22, 0, 5, tzinfo=UTC)
    for source, adapter in adapters.items():
        batch = adapter.normalize(
            tenant_id=tenant_id,
            records=payload[source],
            collected_at=collected_at,
        )
        assert batch.source_system == source
        assert batch.observations or batch.audit_events
