from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

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
    UncertainExecutionRecovery,
    UncertainRecoveryDisposition,
)
from prodkit_control_runtime import InMemoryRecoveryStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


async def main() -> None:
    tenant_id = "dr-game-day"
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

    with (
        tempfile.TemporaryDirectory(prefix="prodkit-dr-source-") as source_tmp,
        tempfile.TemporaryDirectory(prefix="prodkit-dr-target-") as target_tmp,
    ):
        source = Path(source_tmp)
        target = Path(target_tmp)
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
        checkpoint_path = source / "trusted-checkpoint.snapshot"
        checkpoint_path.write_bytes(b"prodkit-v0.7-signed-checkpoint-fixture\n")
        trusted_checkpoint = _sha256(checkpoint_path)
        independent_anchor_path = source.parent / f"{source.name}-independent-anchor.txt"
        independent_anchor_path.write_text(trusted_checkpoint, encoding="utf-8")
        independent_anchor = hashlib.sha256(independent_anchor_path.read_bytes()).hexdigest()

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
            trusted_checkpoint_sha256=trusted_checkpoint,
            trust_anchor_sha256=independent_anchor,
        )

        for component_record in component_records:
            filename = Path(component_record.reference.removeprefix("file://"))
            shutil.copy2(source / filename, target / filename)

        store = InMemoryRecoveryStore()
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
                sha256=_sha256(target / Path(component_record.reference.removeprefix("file://"))),
                observed_at=datetime.now(UTC),
            )
            for component_record in component_records
        )
        scan = await store.verify_restore(
            context=operator,
            restore_id=plan.restore_id,
            observations=observations,
            ledger_chain_tip_sha256=_sha256(target / f"{RecoveryComponent.LEDGER.value}.snapshot"),
            trust_anchor_sha256=hashlib.sha256(independent_anchor_path.read_bytes()).hexdigest(),
        )
        if (
            not scan.chain_verified
            or not scan.trust_anchor_verified
            or not scan.object_store_verified
        ):
            raise AssertionError("restored assurance state did not verify")

        uncertain_at = started - timedelta(seconds=5)
        uncertain = ExecutionAttemptRecord(
            attempt_id=uuid4(),
            action_id=uuid4(),
            run_id=uuid4(),
            tenant_id=tenant_id,
            idempotency_key="dr-game-day-uncertain",
            action_digest="d" * 64,
            executor_name="deployment",
            executor_version="1.0.0",
            executor_identity="spiffe://prodkit/qualification/deployment",
            state=ExecutionAttemptState.UNCERTAIN,
            claimed_at=uncertain_at,
            started_at=uncertain_at,
            finished_at=uncertain_at,
            provider_operation_id="deployment-qualification-42",
            uncertainty_reason="simulated site loss after provider acceptance",
        )
        recoveries = await store.reconcile_uncertain_attempts(
            context=operator,
            restore_id=plan.restore_id,
            attempts=(uncertain,),
            resolver=_ProviderResolver(),
        )
        if any(item.replay_permitted for item in recoveries):
            raise AssertionError("game day authorized blind replay of an uncertain action")

        result = await store.complete_restore(
            context=operator,
            restore_id=plan.restore_id,
            scan_id=scan.scan_id,
            started_at=started,
        )
        if result.status is not RestoreStatus.VERIFIED or not result.promoted:
            raise AssertionError(f"game-day restore was not promotable: {result.status.value}")
        exercise = await store.record_game_day(
            context=admin,
            result=result,
            started_at=started,
            simulated_site_failure=True,
            notes=(
                "provider-neutral backup bytes restored into isolated target",
                "independent anchor retained outside restored backup set",
                "uncertain execution reconciled by provider operation identity",
            ),
        )
        if not exercise.passed or exercise.blind_replay_count != 0:
            raise AssertionError("v0.7 disaster-recovery game day failed")
        print(
            "DR game day passed: "
            f"RPO={exercise.achieved_rpo_seconds:.3f}s "
            f"RTO={exercise.achieved_rto_seconds:.3f}s "
            "chain=verified anchor=verified uncertain=reconciled blind_replay=0"
        )

        independent_anchor_path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
