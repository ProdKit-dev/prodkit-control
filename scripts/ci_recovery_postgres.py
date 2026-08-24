from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    AuthorizationDeniedError,
    BackupComponentRecord,
    BackupManifest,
    BreakGlassCapability,
    BreakGlassGrant,
    GameDayExercise,
    IntegrityScanResult,
    RecoveryAuditEvent,
    RecoveryAuditEventType,
    RecoveryComponent,
    RecoveryIntegrityStatus,
    ReliabilityProfile,
    RestorePlan,
    RestoreResult,
    RestoreStatus,
    TenantAccessContext,
    TenantCapability,
    UncertainExecutionRecovery,
    UncertainRecoveryDisposition,
)
from prodkit_control_postgres import PostgresRecoveryStore, assert_schema_compatible


def _connection_values() -> tuple[str, int, str, str, str]:
    return (
        os.environ["PRODKIT_POSTGRES_HOST"],
        int(os.environ["PRODKIT_POSTGRES_PORT"]),
        os.environ["PRODKIT_POSTGRES_DATABASE"],
        os.environ["PRODKIT_POSTGRES_USER"],
        os.environ["PRODKIT_POSTGRES_PASSWORD"],
    )


def _actor(tenant_id: str, actor_id: str) -> ActorRef:
    return ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant_id)


def _context(tenant_id: str, actor_id: str) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant_id,
        actor=_actor(tenant_id, actor_id),
        capabilities=(TenantCapability.READ,),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


async def _qualify_store(sessions: async_sessionmaker[AsyncSession]) -> None:
    await assert_schema_compatible(sessions)
    tenant_id = "ci-recovery-a"
    foreign_tenant = "ci-recovery-b"
    now = datetime.now(UTC)
    admin = _actor(tenant_id, "recovery-admin")
    operator = _actor(tenant_id, "recovery-operator")
    store = PostgresRecoveryStore(sessions)

    profile = ReliabilityProfile(
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
        created_by=admin,
    )
    await store.save_profile(profile)
    assert await store.current_profile(tenant_id) == profile
    assert await store.current_profile(foreign_tenant) is None

    components = tuple(
        BackupComponentRecord(
            component=component,
            reference=f"s3://ci-backup/{component.value}",
            sha256=f"{index + 1:064x}",
            size_bytes=4096 + index,
            captured_at=now,
            source_site="primary-eu",
        )
        for index, component in enumerate(RecoveryComponent)
    )
    backup = BackupManifest(
        backup_id=uuid4(),
        tenant_id=tenant_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        source_schema_version=8,
        source_control_version="0.7.0",
        source_site="primary-eu",
        snapshot_set_id="ci-snapshot-set",
        recovery_point_at=now - timedelta(seconds=30),
        created_at=now,
        components=components,
        ledger_chain_tip_sha256="a" * 64,
        trusted_checkpoint_sha256="b" * 64,
        trust_anchor_sha256="c" * 64,
    )
    await store.save_backup(backup)
    await store.save_backup(backup)
    assert await store.get_backup(tenant_id, backup.backup_id) == backup
    assert await store.get_backup(foreign_tenant, backup.backup_id) is None

    grant = BreakGlassGrant(
        grant_id=uuid4(),
        tenant_id=tenant_id,
        operator=operator,
        approved_by=admin,
        capabilities=(BreakGlassCapability.RESTORE, BreakGlassCapability.RECONCILE),
        reason="CI disaster-recovery qualification",
        ticket_reference="DR-CI-1",
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    await store.save_break_glass_grant(grant)
    operator_context = _context(tenant_id, operator.id)
    use = await store.use_break_glass(
        context=operator_context,
        grant_id=grant.grant_id,
        capability=BreakGlassCapability.RESTORE,
        purpose="qualify restore authorization",
    )
    assert use.grant_id == grant.grant_id
    try:
        await store.use_break_glass(
            context=operator_context,
            grant_id=grant.grant_id,
            capability=BreakGlassCapability.FAILOVER,
            purpose="unauthorized capability",
        )
    except AuthorizationDeniedError:
        pass
    else:
        raise AssertionError("break-glass capability escalation must fail")

    restore = RestorePlan(
        restore_id=uuid4(),
        tenant_id=tenant_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        backup_id=backup.backup_id,
        target_site="recovery-eu",
        requested_at=now,
        requested_by=operator,
        break_glass_grant_id=grant.grant_id,
    )
    await store.save_restore_plan(restore)
    assert await store.get_restore_plan(tenant_id, restore.restore_id) == restore
    assert await store.get_restore_plan(foreign_tenant, restore.restore_id) is None

    scan = IntegrityScanResult(
        scan_id=uuid4(),
        restore_id=restore.restore_id,
        tenant_id=tenant_id,
        completed_at=now,
        status=RecoveryIntegrityStatus.VERIFIED,
        chain_verified=True,
        trust_anchor_verified=True,
        object_store_verified=True,
        components_verified=tuple(RecoveryComponent),
    )
    await store.save_integrity_scan(scan)

    uncertain = UncertainExecutionRecovery(
        recovery_id=uuid4(),
        restore_id=restore.restore_id,
        tenant_id=tenant_id,
        attempt_id=uuid4(),
        action_id=uuid4(),
        run_id=uuid4(),
        provider_operation_id="ci-provider-operation",
        disposition=UncertainRecoveryDisposition.MATCHED_SUCCESS,
        observed_at=now,
        evidence_reference="provider://ci/operation",
    )
    await store.save_uncertain_recovery(uncertain)
    assert await store.list_uncertain_recoveries(tenant_id, restore.restore_id) == (uncertain,)
    assert await store.list_uncertain_recoveries(foreign_tenant, restore.restore_id) == ()

    result = RestoreResult(
        restore_id=restore.restore_id,
        tenant_id=tenant_id,
        backup_id=backup.backup_id,
        started_at=now,
        completed_at=now + timedelta(seconds=10),
        status=RestoreStatus.VERIFIED,
        actual_rpo_seconds=30,
        actual_rto_seconds=10,
        integrity_scan_id=scan.scan_id,
        uncertain_recoveries=(uncertain,),
        promoted=True,
        completed_by=operator,
    )
    await store.save_restore_result(result)

    exercise = GameDayExercise(
        exercise_id=uuid4(),
        tenant_id=tenant_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        backup_id=backup.backup_id,
        restore_id=restore.restore_id,
        started_at=now,
        completed_at=now + timedelta(seconds=10),
        simulated_site_failure=True,
        achieved_rpo_seconds=30,
        achieved_rto_seconds=10,
        chain_verified=True,
        trust_anchor_verified=True,
        uncertain_actions_reconciled=True,
        blind_replay_count=0,
        passed=True,
        notes=("PostgreSQL 18 recovery qualification",),
    )
    await store.save_game_day(exercise)

    audit = RecoveryAuditEvent(
        event_id=uuid4(),
        tenant_id=tenant_id,
        event_type=RecoveryAuditEventType.GAME_DAY_RECORDED,
        actor=admin,
        occurred_at=exercise.completed_at,
        target_id=str(exercise.exercise_id),
        reason="record CI recovery game day",
        ticket_reference="DR-CI-1",
        attributes={"passed": "true"},
    )
    await store.append_audit(audit)
    assert await store.list_audit(tenant_id) == (audit,)
    assert await store.list_audit(foreign_tenant) == ()

    await store.revoke_break_glass(
        context=_context(tenant_id, admin.id),
        grant_id=grant.grant_id,
        reason="qualification complete",
    )
    try:
        await store.use_break_glass(
            context=operator_context,
            grant_id=grant.grant_id,
            capability=BreakGlassCapability.RESTORE,
            purpose="use after revocation",
        )
    except AuthorizationDeniedError:
        pass
    else:
        raise AssertionError("revoked break-glass grant must fail closed")


async def _qualify_append_only() -> None:
    host, port, database, user, password = _connection_values()
    connection = await asyncpg.connect(
        host=host, port=port, database=database, user=user, password=password
    )
    try:
        for statement in (
            "UPDATE recovery_profiles SET rto_seconds = rto_seconds + 1",
            "DELETE FROM recovery_backup_manifests",
            "UPDATE recovery_restore_results SET status = 'failed'",
            "DELETE FROM recovery_audit_events",
        ):
            try:
                await connection.execute(statement)
            except asyncpg.RaiseError:
                pass
            else:
                raise AssertionError(
                    f"recovery evidence mutation unexpectedly succeeded: {statement}"
                )
    finally:
        await connection.close()


async def main() -> None:
    host, port, database, user, password = _connection_values()
    engine = create_async_engine(
        f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}",
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _qualify_store(sessions)
        await _qualify_append_only()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
