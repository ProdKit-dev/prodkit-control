from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    AuthorizationDeniedError,
    BackupComponentRecord,
    BackupManifest,
    BreakGlassCapability,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    RecoveryComponent,
    RecoveryIntegrityStatus,
    ReliabilityProfile,
    RestoredComponentObservation,
    RestoreStatus,
    TenantAccessContext,
    TenantCapability,
    UncertainExecutionRecovery,
    UncertainRecoveryDisposition,
)
from prodkit_control_runtime import InMemoryRecoveryStore, RecoveryIntegrityVerifier


def _actor(tenant_id: str, actor_id: str) -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant_id)


def _context(
    tenant_id: str,
    actor_id: str,
    *capabilities: TenantCapability,
) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant_id,
        actor=_actor(tenant_id, actor_id),
        capabilities=capabilities,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


def _profile(tenant_id: str, actor: ActorRef, now: datetime) -> ReliabilityProfile:
    return ReliabilityProfile(
        profile_id="enterprise-warm-standby",
        tenant_id=tenant_id,
        revision=1,
        rpo_seconds=300,
        rto_seconds=3600,
        backup_interval_seconds=300,
        max_backup_age_seconds=900,
        restore_exercise_interval_seconds=86400,
        required_components=tuple(RecoveryComponent),
        max_break_glass_seconds=900,
        effective_at=now,
        created_at=now,
        created_by=actor,
    )


def _manifest(tenant_id: str, now: datetime) -> BackupManifest:
    components = tuple(
        BackupComponentRecord(
            component=component,
            reference=f"backup://{component.value}",
            sha256=f"{index + 1:064x}",
            size_bytes=1024 + index,
            captured_at=now,
            source_site="primary-eu",
        )
        for index, component in enumerate(RecoveryComponent)
    )
    return BackupManifest(
        backup_id=uuid4(),
        tenant_id=tenant_id,
        profile_id="enterprise-warm-standby",
        profile_revision=1,
        source_schema_version=8,
        source_control_version="0.7.0",
        source_site="primary-eu",
        snapshot_set_id="snapshot-set-1",
        recovery_point_at=now - timedelta(seconds=30),
        created_at=now,
        components=components,
        ledger_chain_tip_sha256="a" * 64,
        trusted_checkpoint_sha256="b" * 64,
        trust_anchor_sha256="c" * 64,
    )


class _Resolver:
    async def reconcile(
        self,
        *,
        attempt: ExecutionAttemptRecord,
        restore_id: UUID,
    ) -> UncertainExecutionRecovery:
        return UncertainExecutionRecovery(
            recovery_id=uuid4(),
            restore_id=restore_id,
            tenant_id=attempt.tenant_id,
            attempt_id=attempt.attempt_id,
            action_id=attempt.action_id,
            run_id=attempt.run_id,
            provider_operation_id=attempt.provider_operation_id,
            disposition=UncertainRecoveryDisposition.MATCHED_SUCCESS,
            observed_at=datetime.now(UTC),
            evidence_reference="provider://operation/confirmed",
        )


@pytest.mark.asyncio
async def test_recovery_game_day_verifies_chain_and_reconciles_uncertain_execution() -> None:
    store = InMemoryRecoveryStore()
    tenant_id = "tenant-a"
    admin = _context(
        tenant_id,
        "admin",
        TenantCapability.READ,
        TenantCapability.WRITE,
        TenantCapability.APPROVE,
        TenantCapability.CONFIGURE,
    )
    operator = _context(tenant_id, "recovery-operator", TenantCapability.READ)
    now = datetime.now(UTC)
    profile = _profile(tenant_id, admin.actor, now)
    await store.publish_profile(context=admin, profile=profile)
    manifest = _manifest(tenant_id, now)
    await store.record_backup(context=admin, manifest=manifest)

    grant = await store.issue_break_glass(
        context=admin,
        operator=operator.actor,
        capabilities=(
            BreakGlassCapability.RESTORE,
            BreakGlassCapability.INTEGRITY_SCAN,
            BreakGlassCapability.RECONCILE,
        ),
        reason="exercise enterprise DR",
        ticket_reference="DR-100",
        ttl_seconds=600,
    )
    plan = await store.plan_restore(
        context=operator,
        backup_id=manifest.backup_id,
        target_site="recovery-eu",
        grant_id=grant.grant_id,
    )
    observations = tuple(
        RestoredComponentObservation(
            component=item.component,
            reference=item.reference,
            sha256=item.sha256,
            observed_at=datetime.now(UTC),
        )
        for item in manifest.components
    )
    scan = await store.verify_restore(
        context=operator,
        restore_id=plan.restore_id,
        observations=observations,
        ledger_chain_tip_sha256=manifest.ledger_chain_tip_sha256,
        trust_anchor_sha256=manifest.trust_anchor_sha256,
    )
    assert scan.status is RecoveryIntegrityStatus.VERIFIED

    attempt_time = datetime.now(UTC) - timedelta(minutes=1)
    attempt = ExecutionAttemptRecord(
        attempt_id=uuid4(),
        action_id=uuid4(),
        run_id=uuid4(),
        tenant_id=tenant_id,
        idempotency_key="recovery-action",
        action_digest="d" * 64,
        executor_name="deployment",
        executor_version="1.0.0",
        executor_identity="spiffe://prodkit/executor/deployment",
        state=ExecutionAttemptState.UNCERTAIN,
        claimed_at=attempt_time,
        started_at=attempt_time,
        finished_at=attempt_time,
        provider_operation_id="provider-op-1",
        uncertainty_reason="site failure after target acceptance",
    )
    recoveries = await store.reconcile_uncertain_attempts(
        context=operator,
        restore_id=plan.restore_id,
        attempts=(attempt,),
        resolver=_Resolver(),
    )
    assert recoveries[0].disposition is UncertainRecoveryDisposition.MATCHED_SUCCESS
    assert not recoveries[0].replay_permitted

    result = await store.complete_restore(
        context=operator,
        restore_id=plan.restore_id,
        scan_id=scan.scan_id,
        started_at=now,
    )
    assert result.status is RestoreStatus.VERIFIED
    assert result.promoted
    exercise = await store.record_game_day(
        context=admin,
        result=result,
        started_at=now,
        simulated_site_failure=True,
        notes=("isolated recovery-site exercise",),
    )
    assert exercise.passed
    assert exercise.blind_replay_count == 0
    assert await store.audit_events(context=admin)


@pytest.mark.asyncio
async def test_break_glass_is_four_eyes_scoped_and_revocable() -> None:
    store = InMemoryRecoveryStore()
    tenant_id = "tenant-a"
    admin = _context(
        tenant_id,
        "admin",
        TenantCapability.READ,
        TenantCapability.APPROVE,
        TenantCapability.CONFIGURE,
    )
    now = datetime.now(UTC)
    await store.publish_profile(context=admin, profile=_profile(tenant_id, admin.actor, now))

    with pytest.raises(ValueError, match="independent approver"):
        await store.issue_break_glass(
            context=admin,
            operator=admin.actor,
            capabilities=(BreakGlassCapability.RESTORE,),
            reason="self-approved emergency access",
            ticket_reference="DR-101",
            ttl_seconds=300,
        )

    operator = _context(tenant_id, "operator", TenantCapability.READ)
    grant = await store.issue_break_glass(
        context=admin,
        operator=operator.actor,
        capabilities=(BreakGlassCapability.RESTORE,),
        reason="controlled recovery",
        ticket_reference="DR-102",
        ttl_seconds=300,
    )
    with pytest.raises(AuthorizationDeniedError, match="capability"):
        await store.use_break_glass(
            context=operator,
            grant_id=grant.grant_id,
            capability=BreakGlassCapability.FAILOVER,
            purpose="unauthorized failover",
        )
    await store.revoke_break_glass(context=admin, grant_id=grant.grant_id, reason="exercise ended")
    with pytest.raises(AuthorizationDeniedError, match="revoked"):
        await store.use_break_glass(
            context=operator,
            grant_id=grant.grant_id,
            capability=BreakGlassCapability.RESTORE,
            purpose="late restore",
        )


def test_integrity_verifier_fails_on_object_or_anchor_drift() -> None:
    now = datetime.now(UTC)
    manifest = _manifest("tenant-a", now)
    observations = tuple(
        RestoredComponentObservation(
            component=item.component,
            reference=item.reference,
            sha256=("f" * 64 if item.component is RecoveryComponent.OBJECT_STORE else item.sha256),
            observed_at=now,
        )
        for item in manifest.components
    )
    scan = RecoveryIntegrityVerifier().verify(
        manifest=manifest,
        restore_id=uuid4(),
        observations=observations,
        ledger_chain_tip_sha256=manifest.ledger_chain_tip_sha256,
        trust_anchor_sha256="e" * 64,
        completed_at=now,
    )
    assert scan.status is RecoveryIntegrityStatus.FAILED
    assert not scan.object_store_verified
    assert not scan.trust_anchor_verified
    assert any(finding.severity.value == "critical" for finding in scan.findings)
