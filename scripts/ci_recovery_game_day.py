from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prodkit_control_core import (
    ActorKind,
    ActorRef,
    BackupComponentRecord,
    BackupManifest,
    BreakGlassCapability,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    RecoveryComponent,
    ReliabilityProfile,
    RestoredComponentObservation,
    RestoreStatus,
    TenantAccessContext,
    TenantCapability,
    TrustRootPolicy,
    UncertainExecutionRecovery,
    UncertainRecoveryDisposition,
    canonical_json_bytes,
    sha256_hex,
)
from prodkit_control_postgres import (
    PostgresExecutionAttemptStore,
    PostgresRecoveryStore,
    assert_schema_compatible,
)
from prodkit_control_runtime import (
    Ed25519CheckpointSigner,
    RecoveryIntegrityVerifier,
    checkpoint_sha256,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


class _ProviderResolver:
    async def reconcile(
        self,
        *,
        attempt: ExecutionAttemptRecord,
        restore_id: UUID,
    ) -> UncertainExecutionRecovery:
        if attempt.provider_operation_id is None:
            raise AssertionError(
                "game-day uncertain attempt must retain provider operation identity"
            )
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
        idempotency_key="dr-game-day-uncertain",
        action_digest="d" * 64,
        executor_name="deployment",
        executor_version="1.0.0",
        executor_identity="spiffe://prodkit/qualification/deployment",
        state=state,
        claimed_at=claimed_at,
        started_at=started_at,
        finished_at=finished_at,
        provider_operation_id=provider_operation_id,
        uncertainty_reason=uncertainty_reason,
    )


async def main() -> None:
    host, port, database, user, password = _connection_values()
    engine = create_async_engine(
        f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}",
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await assert_schema_compatible(sessions)

    tenant_id = f"dr-game-day-{uuid4().hex[:8]}"
    admin = _context(
        tenant_id,
        "dr-admin",
        TenantCapability.READ,
        TenantCapability.WRITE,
        TenantCapability.APPROVE,
        TenantCapability.CONFIGURE,
    )
    operator = _context(tenant_id, "dr-operator", TenantCapability.READ)
    started = datetime.now(UTC)

    try:
        with (
            tempfile.TemporaryDirectory(prefix="prodkit-dr-source-") as source_tmp,
            tempfile.TemporaryDirectory(prefix="prodkit-dr-target-") as target_tmp,
            tempfile.TemporaryDirectory(prefix="prodkit-dr-anchor-") as anchor_tmp,
        ):
            source = Path(source_tmp)
            target = Path(target_tmp)
            anchor_dir = Path(anchor_tmp)
            component_records: list[BackupComponentRecord] = []
            for index, component in enumerate(RecoveryComponent):
                path = source / f"{component.value}.snapshot"
                path.write_bytes(
                    (
                        f"prodkit-control-dr-v0.7\ncomponent={component.value}\n"
                        f"ordinal={index}\ntenant={tenant_id}\n"
                    ).encode()
                )
                component_records.append(
                    BackupComponentRecord(
                        component=component,
                        reference=f"file://{path.name}",
                        sha256=_sha256(path),
                        size_bytes=path.stat().st_size,
                        captured_at=started,
                        source_site="qualification-primary",
                    )
                )

            ledger_path = source / f"{RecoveryComponent.LEDGER.value}.snapshot"
            ledger_chain_tip = _sha256(ledger_path)
            signer = Ed25519CheckpointSigner.generate(
                key_id="dr-game-day-key", signer_id="dr-game-day-signer"
            )
            trust = TrustRootPolicy(
                policy_id="dr-game-day-trust",
                revision="1",
                trusted_keys=(signer.trusted_key(valid_from=started - timedelta(days=1)),),
                allowed_signers=("dr-game-day-signer",),
            )
            checkpoint = signer.sign(
                run_id=uuid4(),
                tenant_id=tenant_id,
                created_at=started,
                sequence=1,
                final_event_hash=ledger_chain_tip,
                evidence_bundle_sha256="e" * 64,
            )
            checkpoint_digest = checkpoint_sha256(checkpoint)
            trust_digest = sha256_hex(trust)

            # These anchors are deliberately kept outside the restored component directory.
            (anchor_dir / "trust-root.json").write_bytes(canonical_json_bytes(trust))
            (anchor_dir / "checkpoint.sha256").write_text(checkpoint_digest, encoding="utf-8")
            if _sha256(anchor_dir / "trust-root.json") != trust_digest:
                raise AssertionError("independent trust-root canonical digest is unstable")

            manifest = BackupManifest(
                backup_id=uuid4(),
                tenant_id=tenant_id,
                profile_id="enterprise-warm-standby",
                profile_revision=1,
                source_schema_version=8,
                source_control_version="0.7.0",
                source_site="qualification-primary",
                snapshot_set_id=f"game-day-{uuid4().hex}",
                recovery_point_at=started - timedelta(seconds=30),
                created_at=started,
                components=tuple(component_records),
                ledger_chain_tip_sha256=ledger_chain_tip,
                trusted_checkpoint_sha256=checkpoint_digest,
                trust_anchor_sha256=trust_digest,
            )

            for component_record in component_records:
                filename = Path(component_record.reference.removeprefix("file://"))
                shutil.copy2(source / filename, target / filename)

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
                effective_at=started,
                created_at=started,
                created_by=admin.actor,
            )
            await store.publish_profile(context=admin, profile=profile)
            await store.record_backup(context=admin, manifest=manifest)
            grant = await store.issue_break_glass(
                context=admin,
                operator=operator.actor,
                capabilities=(
                    BreakGlassCapability.RESTORE,
                    BreakGlassCapability.INTEGRITY_SCAN,
                    BreakGlassCapability.RECONCILE,
                    BreakGlassCapability.FAILOVER,
                ),
                reason="automated v0.7 disaster-recovery game day",
                ticket_reference="DR-GAME-DAY-CI",
                ttl_seconds=600,
            )
            plan = await store.plan_restore(
                context=operator,
                backup_id=manifest.backup_id,
                target_site="qualification-recovery",
                grant_id=grant.grant_id,
            )

            observations = tuple(
                RestoredComponentObservation(
                    component=component_record.component,
                    reference=f"file://{Path(component_record.reference).name}",
                    sha256=_sha256(
                        target / Path(component_record.reference.removeprefix("file://"))
                    ),
                    observed_at=datetime.now(UTC),
                )
                for component_record in component_records
            )
            scan = await store.verify_restore(
                context=operator,
                restore_id=plan.restore_id,
                observations=observations,
                ledger_chain_tip_sha256=_sha256(
                    target / f"{RecoveryComponent.LEDGER.value}.snapshot"
                ),
                checkpoint=checkpoint,
                trust_policy=trust,
                verifier=RecoveryIntegrityVerifier(),
            )
            if (
                not scan.chain_verified
                or not scan.checkpoint_verified
                or not scan.trust_anchor_verified
                or not scan.object_store_verified
            ):
                raise AssertionError("restored assurance state did not verify")

            attempt_store = PostgresExecutionAttemptStore(sessions)
            attempt_id, action_id, run_id = uuid4(), uuid4(), uuid4()
            claimed_at = datetime.now(UTC)
            await attempt_store.create(
                _attempt(
                    tenant_id=tenant_id,
                    attempt_id=attempt_id,
                    action_id=action_id,
                    run_id=run_id,
                    claimed_at=claimed_at,
                    state=ExecutionAttemptState.CLAIMED,
                )
            )
            started_at = datetime.now(UTC)
            await attempt_store.replace(
                _attempt(
                    tenant_id=tenant_id,
                    attempt_id=attempt_id,
                    action_id=action_id,
                    run_id=run_id,
                    claimed_at=claimed_at,
                    state=ExecutionAttemptState.STARTED,
                    started_at=started_at,
                    provider_operation_id="deployment-qualification-42",
                )
            )
            await attempt_store.replace(
                _attempt(
                    tenant_id=tenant_id,
                    attempt_id=attempt_id,
                    action_id=action_id,
                    run_id=run_id,
                    claimed_at=claimed_at,
                    state=ExecutionAttemptState.UNCERTAIN,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    provider_operation_id="deployment-qualification-42",
                    uncertainty_reason="simulated site loss after provider acceptance",
                )
            )
            recoveries = await store.reconcile_uncertain_attempts(
                context=operator,
                restore_id=plan.restore_id,
                resolver=_ProviderResolver(),
            )
            if len(recoveries) != 1 or any(item.replay_permitted for item in recoveries):
                raise AssertionError("game day did not safely reconcile durable uncertainty")

            gap = await store.record_recovery_gap(
                context=operator,
                restore_id=plan.restore_id,
                source_references=(
                    "provider-audit://deployment/qualification",
                    "artifact-registry://qualification",
                    "control-edge-log://qualification",
                ),
                unexpected_effect_count=1,
                unresolved_effect_count=0,
                evidence_reference="reconciliation://game-day/rpo-gap",
            )
            if gap.unresolved_effect_count:
                raise AssertionError("RPO gap remains unresolved")

            result = await store.complete_restore(
                context=operator,
                restore_id=plan.restore_id,
                scan_id=scan.scan_id,
            )
            if result.status is not RestoreStatus.VERIFIED or not result.promoted:
                raise AssertionError(f"game-day restore was not promotable: {result.status.value}")
            exercise = await store.record_game_day(
                context=admin,
                result=result,
                simulated_site_failure=True,
                notes=(
                    "provider-neutral backup bytes restored into isolated target",
                    "signed checkpoint verified against independently retained trust root",
                    "durable uncertain execution reconciled by provider operation identity",
                    "RPO gap reconciled against independent provider and registry evidence",
                    "PostgreSQL recovery catalog persisted the full exercise",
                ),
            )
            durable = await store.get_game_day(context=admin, exercise_id=exercise.exercise_id)
            if durable != exercise or not exercise.passed or exercise.blind_replay_count != 0:
                raise AssertionError("v0.7 durable disaster-recovery game day failed")
            if not exercise.durable_catalog_verified or not exercise.recovery_gap_reconciled:
                raise AssertionError("game day did not prove durable gap reconciliation")
            print(
                "DR game day passed: "
                f"RPO={exercise.achieved_rpo_seconds:.3f}s "
                f"RTO={exercise.achieved_rto_seconds:.3f}s "
                "chain=verified checkpoint=verified anchor=verified object_store=verified "
                "uncertain=reconciled rpo_gap=reconciled durable_catalog=verified blind_replay=0"
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
