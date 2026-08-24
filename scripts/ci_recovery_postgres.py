from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    ReliabilityProfile,
    RestoredComponentObservation,
    RestoreStatus,
    RunRecord,
    RunStatus,
    TenantAccessContext,
    TenantCapability,
    TrustRootPolicy,
    UncertainExecutionRecovery,
    UncertainRecoveryDisposition,
    sha256_hex,
)
from prodkit_control_postgres import (
    PostgresExecutionAttemptStore,
    PostgresRecoveryStore,
    PostgresRunStore,
    assert_schema_compatible,
)
from prodkit_control_runtime import (
    Ed25519CheckpointSigner,
    RecoveryIntegrityVerifier,
    checkpoint_sha256,
)


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


def _context(tenant_id: str, actor_id: str, *capabilities: TenantCapability) -> TenantAccessContext:
    now = datetime.now(UTC)
    return TenantAccessContext(
        tenant_id=tenant_id,
        actor=_actor(tenant_id, actor_id),
        capabilities=capabilities,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


class _Resolver:
    async def reconcile(
        self,
        *,
        attempt: ExecutionAttemptRecord,
        restore_id: UUID,
    ) -> UncertainExecutionRecovery:
        if attempt.provider_operation_id is None:
            raise AssertionError("uncertain execution lost provider operation identity")
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
            evidence_reference=f"provider://observed/{attempt.provider_operation_id}",
        )


def _attempt(
    *,
    tenant_id: str,
    attempt_id: UUID,
    action_id: UUID,
    run_id: UUID,
    claimed_at: datetime,
    state: ExecutionAttemptState,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    provider_operation_id: str | None = None,
    uncertainty_reason: str | None = None,
) -> ExecutionAttemptRecord:
    return ExecutionAttemptRecord(
        attempt_id=attempt_id,
        action_id=action_id,
        run_id=run_id,
        tenant_id=tenant_id,
        idempotency_key="dr-ci-uncertain-action",
        action_digest="d" * 64,
        executor_name="deployment",
        executor_version="1.0.0",
        executor_identity="spiffe://prodkit/ci/deployment",
        state=state,
        claimed_at=claimed_at,
        started_at=started_at,
        finished_at=finished_at,
        provider_operation_id=provider_operation_id,
        uncertainty_reason=uncertainty_reason,
    )


async def _qualify_store(sessions: async_sessionmaker[AsyncSession]) -> None:
    await assert_schema_compatible(sessions)
    tenant_id = f"ci-recovery-{uuid4().hex[:8]}"
    foreign_tenant = f"ci-recovery-foreign-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    admin = _context(
        tenant_id,
        "recovery-admin",
        TenantCapability.READ,
        TenantCapability.WRITE,
        TenantCapability.APPROVE,
        TenantCapability.CONFIGURE,
    )
    operator = _context(tenant_id, "recovery-operator", TenantCapability.READ)
    foreign_reader = _context(foreign_tenant, "foreign-reader", TenantCapability.READ)
    store = PostgresRecoveryStore(sessions)

    profile = ReliabilityProfile(
        profile_id="enterprise-warm-standby",
        tenant_id=tenant_id,
        revision=1,
        rpo_seconds=300,
        rto_seconds=3600,
        backup_interval_seconds=300,
        max_backup_age_seconds=900,
        restore_exercise_interval_seconds=604800,
        required_components=tuple(RecoveryComponent),
        max_break_glass_seconds=900,
        effective_at=now,
        created_at=now,
        created_by=admin.actor,
    )
    await store.publish_profile(context=admin, profile=profile)
    assert await store.current_profile(context=admin) == profile
    assert await store.current_profile(context=foreign_reader) is None

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
    ledger_chain_tip = "a" * 64
    signer = Ed25519CheckpointSigner.generate(key_id="dr-ci-key", signer_id="dr-ci-signer")
    trust = TrustRootPolicy(
        policy_id="dr-ci-trust",
        revision="1",
        trusted_keys=(signer.trusted_key(valid_from=now - timedelta(days=1)),),
        allowed_signers=("dr-ci-signer",),
    )
    checkpoint = signer.sign(
        run_id=uuid4(),
        tenant_id=tenant_id,
        created_at=now,
        sequence=1,
        final_event_hash=ledger_chain_tip,
        evidence_bundle_sha256="e" * 64,
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
        ledger_chain_tip_sha256=ledger_chain_tip,
        trusted_checkpoint_sha256=checkpoint_sha256(checkpoint),
        trust_anchor_sha256=sha256_hex(trust),
    )
    await store.record_backup(context=admin, manifest=backup)
    await store.record_backup(context=admin, manifest=backup)
    latest = await store.latest_usable_backup(context=admin)
    assert latest == backup

    grant = await store.issue_break_glass(
        context=admin,
        operator=operator.actor,
        capabilities=(
            BreakGlassCapability.RESTORE,
            BreakGlassCapability.INTEGRITY_SCAN,
            BreakGlassCapability.RECONCILE,
            BreakGlassCapability.FAILOVER,
        ),
        reason="CI disaster-recovery qualification",
        ticket_reference="DR-CI-1",
        ttl_seconds=600,
    )
    try:
        await store.use_break_glass(
            context=operator,
            grant_id=grant.grant_id,
            capability=BreakGlassCapability.CONFIGURE_RECOVERY,
            purpose="unauthorized capability",
        )
    except AuthorizationDeniedError:
        pass
    else:
        raise AssertionError("break-glass capability escalation must fail")

    plan = await store.plan_restore(
        context=operator,
        backup_id=backup.backup_id,
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
        for item in backup.components
    )
    scan = await store.verify_restore(
        context=operator,
        restore_id=plan.restore_id,
        observations=observations,
        ledger_chain_tip_sha256=backup.ledger_chain_tip_sha256,
        checkpoint=checkpoint,
        trust_policy=trust,
        verifier=RecoveryIntegrityVerifier(),
    )
    assert scan.checkpoint_verified and scan.trust_anchor_verified

    attempts = PostgresExecutionAttemptStore(sessions)
    runs = PostgresRunStore(sessions)
    attempt_id, action_id, run_id = uuid4(), uuid4(), uuid4()
    claimed_at = datetime.now(UTC)
    await runs.create(
        RunRecord(
            run_id=run_id,
            tenant_id=tenant_id,
            status=RunStatus.RUNNING,
            initiated_by=admin.actor,
            environment="recovery-ci",
            purpose="qualify durable uncertain execution recovery",
            trace_id=f"recovery-ci-{run_id.hex}",
            started_at=claimed_at,
        )
    )
    claimed = _attempt(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        action_id=action_id,
        run_id=run_id,
        claimed_at=claimed_at,
        state=ExecutionAttemptState.CLAIMED,
    )
    await attempts.create(claimed)
    started_at = datetime.now(UTC)
    started = _attempt(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        action_id=action_id,
        run_id=run_id,
        claimed_at=claimed_at,
        state=ExecutionAttemptState.STARTED,
        started_at=started_at,
        provider_operation_id="provider-ci-42",
    )
    await attempts.replace(started)
    uncertain = _attempt(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        action_id=action_id,
        run_id=run_id,
        claimed_at=claimed_at,
        state=ExecutionAttemptState.UNCERTAIN,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        provider_operation_id="provider-ci-42",
        uncertainty_reason="simulated site loss after provider acceptance",
    )
    await attempts.replace(uncertain)

    recoveries = await store.reconcile_uncertain_attempts(
        context=operator,
        restore_id=plan.restore_id,
        resolver=_Resolver(),
    )
    assert len(recoveries) == 1 and recoveries[0].attempt_id == attempt_id
    assert not recoveries[0].replay_permitted

    gap = await store.record_recovery_gap(
        context=operator,
        restore_id=plan.restore_id,
        source_references=("provider-audit://ci", "deployment-registry://ci"),
        unexpected_effect_count=1,
        unresolved_effect_count=0,
        evidence_reference="reconciliation://ci/rpo-gap",
    )
    assert gap.unresolved_effect_count == 0

    result = await store.complete_restore(
        context=operator,
        restore_id=plan.restore_id,
        scan_id=scan.scan_id,
    )
    assert result.status is RestoreStatus.VERIFIED and result.promoted
    assert result.recovery_gap_reconciled
    assert (
        await store.get_restore_result(context=foreign_reader, restore_id=plan.restore_id) is None
    )

    exercise = await store.record_game_day(
        context=admin,
        result=result,
        simulated_site_failure=True,
        notes=("PostgreSQL 18 governed recovery qualification",),
    )
    assert exercise.passed and exercise.durable_catalog_verified
    assert await store.get_game_day(context=admin, exercise_id=exercise.exercise_id) == exercise
    assert (
        await store.get_game_day(context=foreign_reader, exercise_id=exercise.exercise_id) is None
    )
    assert await store.audit_events(context=admin)
    assert await store.audit_events(context=foreign_reader) == ()

    await store.revoke_break_glass(
        context=admin,
        grant_id=grant.grant_id,
        reason="qualification complete",
    )
    try:
        await store.use_break_glass(
            context=operator,
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
            "UPDATE recovery_gap_reconciliations SET unresolved_effect_count = 99",
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
