from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActorRef,
    AuthorizationDeniedError,
    BackupManifest,
    BreakGlassCapability,
    BreakGlassGrant,
    BreakGlassUse,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    GameDayExercise,
    IntegrityScanResult,
    IntegrityViolationError,
    RecoveryAuditEvent,
    RecoveryAuditEventType,
    RecoveryComponent,
    RecoveryFindingSeverity,
    RecoveryGapReconciliation,
    RecoveryIntegrityFinding,
    RecoveryIntegrityStatus,
    ReliabilityProfile,
    RestoredComponentObservation,
    RestorePlan,
    RestoreResult,
    RestoreStatus,
    SignedCheckpoint,
    TenantAccessContext,
    TenantAccessMode,
    TenantCapability,
    TrustRootPolicy,
    UncertainExecutionRecovery,
    UncertainRecoveryDisposition,
    sha256_hex,
)

from .attestations import OfflineAssuranceVerifier, checkpoint_sha256


class UncertainAttemptResolver(Protocol):
    async def reconcile(
        self,
        *,
        attempt: ExecutionAttemptRecord,
        restore_id: UUID,
    ) -> UncertainExecutionRecovery: ...


class RecoveryIntegrityVerifier:
    """Verify restored bytes, chain state, signed checkpoint, and independent trust root."""

    def __init__(self, assurance_verifier: OfflineAssuranceVerifier | None = None) -> None:
        self._assurance = assurance_verifier or OfflineAssuranceVerifier()

    def verify(
        self,
        *,
        manifest: BackupManifest,
        restore_id: UUID,
        observations: tuple[RestoredComponentObservation, ...],
        ledger_chain_tip_sha256: str,
        checkpoint: SignedCheckpoint,
        trust_policy: TrustRootPolicy,
        completed_at: datetime | None = None,
    ) -> IntegrityScanResult:
        now = completed_at or datetime.now(UTC)
        expected = {item.component: item for item in manifest.components}
        observed = {item.component: item for item in observations}
        if len(observed) != len(observations):
            raise ValueError("restored component observations must be unique")

        findings: list[RecoveryIntegrityFinding] = []
        verified: list[RecoveryComponent] = []
        for component, component_record in expected.items():
            observation = observed.get(component)
            if observation is None:
                findings.append(
                    RecoveryIntegrityFinding(
                        severity=RecoveryFindingSeverity.CRITICAL,
                        component=component,
                        reference=component_record.reference,
                        summary="required recovery component is missing",
                        expected_sha256=component_record.sha256,
                    )
                )
                continue
            if observation.sha256 != component_record.sha256:
                findings.append(
                    RecoveryIntegrityFinding(
                        severity=RecoveryFindingSeverity.CRITICAL,
                        component=component,
                        reference=observation.reference,
                        summary="restored recovery component digest mismatch",
                        expected_sha256=component_record.sha256,
                        observed_sha256=observation.sha256,
                    )
                )
                continue
            verified.append(component)

        for component in set(observed) - set(expected):
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.ERROR,
                    component=component,
                    reference=observed[component].reference,
                    summary="restored component was not present in the canonical backup manifest",
                    observed_sha256=observed[component].sha256,
                )
            )

        chain_verified = ledger_chain_tip_sha256 == manifest.ledger_chain_tip_sha256
        if not chain_verified:
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.CRITICAL,
                    component=RecoveryComponent.LEDGER,
                    reference="ledger-chain-tip",
                    summary="restored ledger chain tip does not match the backup manifest",
                    expected_sha256=manifest.ledger_chain_tip_sha256,
                    observed_sha256=ledger_chain_tip_sha256,
                )
            )

        trust_anchor_digest = sha256_hex(trust_policy)
        trust_anchor_verified = trust_anchor_digest == manifest.trust_anchor_sha256
        if not trust_anchor_verified:
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.CRITICAL,
                    reference="trusted-anchor",
                    summary="independent trust-root policy does not match the backup manifest anchor",
                    expected_sha256=manifest.trust_anchor_sha256,
                    observed_sha256=trust_anchor_digest,
                )
            )

        checkpoint_digest = checkpoint_sha256(checkpoint)
        checkpoint_verified = checkpoint_digest == manifest.trusted_checkpoint_sha256
        if not checkpoint_verified:
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.CRITICAL,
                    reference="signed-checkpoint",
                    summary="restored signed checkpoint digest does not match the backup manifest",
                    expected_sha256=manifest.trusted_checkpoint_sha256,
                    observed_sha256=checkpoint_digest,
                )
            )
        if checkpoint.tenant_id != manifest.tenant_id:
            checkpoint_verified = False
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.CRITICAL,
                    reference="signed-checkpoint-tenant",
                    summary="restored signed checkpoint belongs to another tenant",
                )
            )
        if checkpoint.final_event_hash != manifest.ledger_chain_tip_sha256:
            checkpoint_verified = False
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.CRITICAL,
                    component=RecoveryComponent.LEDGER,
                    reference="signed-checkpoint-chain-tip",
                    summary="signed checkpoint does not bind the restored ledger chain tip",
                    expected_sha256=manifest.ledger_chain_tip_sha256,
                    observed_sha256=checkpoint.final_event_hash,
                )
            )
        if trust_anchor_verified and checkpoint_verified:
            try:
                self._assurance.verify_checkpoint(checkpoint, trust_policy=trust_policy)
            except IntegrityViolationError as exc:
                checkpoint_verified = False
                findings.append(
                    RecoveryIntegrityFinding(
                        severity=RecoveryFindingSeverity.CRITICAL,
                        reference="signed-checkpoint-signature",
                        summary=f"signed checkpoint failed independent verification: {exc}",
                    )
                )

        object_store_expected = RecoveryComponent.OBJECT_STORE in expected
        object_store_verified = (
            not object_store_expected or RecoveryComponent.OBJECT_STORE in verified
        )
        if object_store_expected and not object_store_verified:
            findings.append(
                RecoveryIntegrityFinding(
                    severity=RecoveryFindingSeverity.CRITICAL,
                    component=RecoveryComponent.OBJECT_STORE,
                    reference="object-store",
                    summary="object-store recovery did not verify",
                )
            )

        status = (
            RecoveryIntegrityStatus.VERIFIED
            if not findings
            and chain_verified
            and checkpoint_verified
            and trust_anchor_verified
            and object_store_verified
            else RecoveryIntegrityStatus.FAILED
        )
        return IntegrityScanResult(
            scan_id=uuid4(),
            restore_id=restore_id,
            tenant_id=manifest.tenant_id,
            completed_at=now,
            status=status,
            chain_verified=chain_verified,
            checkpoint_verified=checkpoint_verified,
            trust_anchor_verified=trust_anchor_verified,
            object_store_verified=object_store_verified,
            components_verified=tuple(verified),
            findings=tuple(findings),
        )


class InMemoryRecoveryStore:
    """Standalone recovery control profile; enterprise DR proof uses the durable PostgreSQL profile."""

    def __init__(self) -> None:
        self._profiles: dict[str, list[ReliabilityProfile]] = {}
        self._backups: dict[tuple[str, UUID], BackupManifest] = {}
        self._plans: dict[tuple[str, UUID], RestorePlan] = {}
        self._scans: dict[tuple[str, UUID], IntegrityScanResult] = {}
        self._recoveries: dict[tuple[str, UUID], list[UncertainExecutionRecovery]] = {}
        self._gaps: dict[tuple[str, UUID], RecoveryGapReconciliation] = {}
        self._results: dict[tuple[str, UUID], RestoreResult] = {}
        self._grants: dict[tuple[str, UUID], BreakGlassGrant] = {}
        self._grant_uses: dict[tuple[str, UUID], list[BreakGlassUse]] = {}
        self._revoked_grants: dict[tuple[str, UUID], datetime] = {}
        self._game_days: dict[tuple[str, UUID], GameDayExercise] = {}
        self._audit: dict[str, list[RecoveryAuditEvent]] = {}
        self._lock = asyncio.Lock()
        self._verifier = RecoveryIntegrityVerifier()

    async def publish_profile(
        self, *, context: TenantAccessContext, profile: ReliabilityProfile
    ) -> ReliabilityProfile:
        self._require_tenant(context, TenantCapability.CONFIGURE)
        if profile.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("reliability profile crossed tenant boundary")
        async with self._lock:
            history = self._profiles.setdefault(context.tenant_id, [])
            expected_revision = 1 if not history else history[-1].revision + 1
            if profile.revision != expected_revision:
                raise ValueError(
                    f"reliability profile revision must advance to {expected_revision}"
                )
            history.append(profile)
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.PROFILE_PUBLISHED,
                    actor=context.actor,
                    occurred_at=datetime.now(UTC),
                    target_id=profile.profile_id,
                    reason="publish reliability profile",
                    attributes={"revision": str(profile.revision)},
                )
            )
            return profile

    async def current_profile(self, *, context: TenantAccessContext) -> ReliabilityProfile | None:
        self._require_tenant(context, TenantCapability.READ)
        now = datetime.now(UTC)
        async with self._lock:
            return self._profile_locked(context.tenant_id, now, required=False)

    async def record_backup(
        self, *, context: TenantAccessContext, manifest: BackupManifest
    ) -> BackupManifest:
        self._require_tenant(context, TenantCapability.WRITE)
        if manifest.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("backup manifest crossed tenant boundary")
        now = datetime.now(UTC)
        if manifest.created_at > now:
            raise ValueError("backup manifest creation time cannot be in the future")
        async with self._lock:
            profile = self._profile_locked(context.tenant_id, now)
            assert profile is not None
            if (manifest.profile_id, manifest.profile_revision) != (
                profile.profile_id,
                profile.revision,
            ):
                raise ValueError("backup manifest does not target the active reliability profile")
            component_set = {item.component for item in manifest.components}
            missing = set(profile.required_components) - component_set
            if missing:
                raise ValueError(
                    "backup is missing required components: "
                    + ", ".join(sorted(item.value for item in missing))
                )
            recovery_age = (now - manifest.recovery_point_at).total_seconds()
            if recovery_age < 0 or recovery_age > profile.rpo_seconds:
                raise ValueError("backup recovery point exceeds the declared RPO")
            key = (context.tenant_id, manifest.backup_id)
            existing = self._backups.get(key)
            if existing is not None and existing != manifest:
                raise ValueError("backup manifest identity is immutable")
            self._backups[key] = manifest
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.BACKUP_RECORDED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(manifest.backup_id),
                    reason="record recovery backup",
                    attributes={"snapshot_set_id": manifest.snapshot_set_id},
                )
            )
            return manifest

    async def latest_usable_backup(self, *, context: TenantAccessContext) -> BackupManifest | None:
        self._require_tenant(context, TenantCapability.READ)
        now = datetime.now(UTC)
        async with self._lock:
            profile = self._profile_locked(context.tenant_id, now)
            assert profile is not None
            backups = sorted(
                (
                    item
                    for (tenant_id, _), item in self._backups.items()
                    if tenant_id == context.tenant_id
                    and (item.profile_id, item.profile_revision)
                    == (profile.profile_id, profile.revision)
                ),
                key=lambda item: item.recovery_point_at,
                reverse=True,
            )
            for backup in backups:
                age = (now - backup.recovery_point_at).total_seconds()
                if 0 <= age <= profile.max_backup_age_seconds:
                    return backup
            return None

    async def issue_break_glass(
        self,
        *,
        context: TenantAccessContext,
        operator: ActorRef,
        capabilities: tuple[BreakGlassCapability, ...],
        reason: str,
        ticket_reference: str,
        ttl_seconds: int,
    ) -> BreakGlassGrant:
        self._require_tenant(context, TenantCapability.APPROVE)
        now = datetime.now(UTC)
        async with self._lock:
            profile = self._profile_locked(context.tenant_id, now)
            assert profile is not None
            if ttl_seconds < 60 or ttl_seconds > profile.max_break_glass_seconds:
                raise ValueError("break-glass TTL is outside the reliability profile")
            grant = BreakGlassGrant(
                grant_id=uuid4(),
                tenant_id=context.tenant_id,
                operator=operator,
                approved_by=context.actor,
                capabilities=capabilities,
                reason=reason,
                ticket_reference=ticket_reference,
                issued_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            self._grants[(context.tenant_id, grant.grant_id)] = grant
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.BREAK_GLASS_ISSUED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(grant.grant_id),
                    reason=reason,
                    ticket_reference=ticket_reference,
                    attributes={"operator": operator.id},
                )
            )
            return grant

    async def revoke_break_glass(
        self, *, context: TenantAccessContext, grant_id: UUID, reason: str
    ) -> None:
        self._require_tenant(context, TenantCapability.APPROVE)
        now = datetime.now(UTC)
        async with self._lock:
            grant = self._grant_locked(context.tenant_id, grant_id)
            self._revoked_grants[(context.tenant_id, grant_id)] = now
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.BREAK_GLASS_REVOKED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(grant_id),
                    reason=reason,
                    ticket_reference=grant.ticket_reference,
                )
            )

    async def use_break_glass(
        self,
        *,
        context: TenantAccessContext,
        grant_id: UUID,
        capability: BreakGlassCapability,
        purpose: str,
    ) -> BreakGlassUse:
        self._require_tenant_mode(context)
        now = datetime.now(UTC)
        async with self._lock:
            grant = self._grant_locked(context.tenant_id, grant_id)
            if (context.tenant_id, grant_id) in self._revoked_grants:
                raise AuthorizationDeniedError("break-glass grant is revoked")
            if now >= grant.expires_at:
                raise AuthorizationDeniedError("break-glass grant is expired")
            if capability not in grant.capabilities:
                raise AuthorizationDeniedError("break-glass capability was not granted")
            if context.actor.kind != grant.operator.kind or context.actor.id != grant.operator.id:
                raise AuthorizationDeniedError("break-glass grant belongs to another operator")
            use = BreakGlassUse(
                use_id=uuid4(),
                grant_id=grant_id,
                tenant_id=context.tenant_id,
                capability=capability,
                actor=context.actor,
                occurred_at=now,
                purpose=purpose,
            )
            self._grant_uses.setdefault((context.tenant_id, grant_id), []).append(use)
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.BREAK_GLASS_USED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(grant_id),
                    reason=purpose,
                    ticket_reference=grant.ticket_reference,
                    attributes={"capability": capability.value},
                )
            )
            return use

    async def plan_restore(
        self,
        *,
        context: TenantAccessContext,
        backup_id: UUID,
        target_site: str,
        grant_id: UUID,
    ) -> RestorePlan:
        await self.use_break_glass(
            context=context,
            grant_id=grant_id,
            capability=BreakGlassCapability.RESTORE,
            purpose=f"restore backup {backup_id} to {target_site}",
        )
        now = datetime.now(UTC)
        async with self._lock:
            backup = self._backups.get((context.tenant_id, backup_id))
            if backup is None:
                raise KeyError(backup_id)
            profile = self._profile_locked(context.tenant_id, now)
            assert profile is not None
            if (backup.profile_id, backup.profile_revision) != (
                profile.profile_id,
                profile.revision,
            ):
                raise ValueError("restore backup does not target the active reliability profile")
            plan = RestorePlan(
                restore_id=uuid4(),
                tenant_id=context.tenant_id,
                profile_id=profile.profile_id,
                profile_revision=profile.revision,
                backup_id=backup_id,
                target_site=target_site,
                failure_detected_at=now,
                requested_at=now,
                requested_by=context.actor,
                break_glass_grant_id=grant_id,
            )
            self._plans[(context.tenant_id, plan.restore_id)] = plan
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.RESTORE_PLANNED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(plan.restore_id),
                    reason="plan disaster recovery restore",
                    attributes={"target_site": target_site, "backup_id": str(backup_id)},
                )
            )
            return plan

    async def verify_restore(
        self,
        *,
        context: TenantAccessContext,
        restore_id: UUID,
        observations: tuple[RestoredComponentObservation, ...],
        ledger_chain_tip_sha256: str,
        checkpoint: SignedCheckpoint,
        trust_policy: TrustRootPolicy,
    ) -> IntegrityScanResult:
        async with self._lock:
            plan = self._plan_locked(context.tenant_id, restore_id)
            manifest = self._backups[(context.tenant_id, plan.backup_id)]
        await self.use_break_glass(
            context=context,
            grant_id=plan.break_glass_grant_id,
            capability=BreakGlassCapability.INTEGRITY_SCAN,
            purpose=f"verify restored assurance state for {restore_id}",
        )
        scan = self._verifier.verify(
            manifest=manifest,
            restore_id=restore_id,
            observations=observations,
            ledger_chain_tip_sha256=ledger_chain_tip_sha256,
            checkpoint=checkpoint,
            trust_policy=trust_policy,
        )
        async with self._lock:
            self._scans[(context.tenant_id, scan.scan_id)] = scan
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.INTEGRITY_SCAN_RECORDED,
                    actor=context.actor,
                    occurred_at=scan.completed_at,
                    target_id=str(scan.scan_id),
                    reason="verify post-restore assurance integrity",
                    attributes={"status": scan.status.value, "restore_id": str(restore_id)},
                )
            )
        return scan

    async def reconcile_uncertain_attempts(
        self,
        *,
        context: TenantAccessContext,
        restore_id: UUID,
        attempts: tuple[ExecutionAttemptRecord, ...],
        resolver: UncertainAttemptResolver,
    ) -> tuple[UncertainExecutionRecovery, ...]:
        async with self._lock:
            plan = self._plan_locked(context.tenant_id, restore_id)
        await self.use_break_glass(
            context=context,
            grant_id=plan.break_glass_grant_id,
            capability=BreakGlassCapability.RECONCILE,
            purpose=f"reconcile uncertain execution after restore {restore_id}",
        )
        identities = [attempt.attempt_id for attempt in attempts]
        if len(identities) != len(set(identities)):
            raise ValueError("uncertain recovery candidates must have unique attempt identities")
        records: list[UncertainExecutionRecovery] = []
        for attempt in attempts:
            if attempt.tenant_id != context.tenant_id:
                raise AuthorizationDeniedError("uncertain attempt crossed tenant boundary")
            if attempt.state is not ExecutionAttemptState.UNCERTAIN:
                raise ValueError("only uncertain execution attempts are recovery candidates")
            record = await resolver.reconcile(attempt=attempt, restore_id=restore_id)
            if (
                record.tenant_id != context.tenant_id
                or record.restore_id != restore_id
                or record.attempt_id != attempt.attempt_id
                or record.action_id != attempt.action_id
                or record.run_id != attempt.run_id
                or record.replay_permitted
            ):
                raise ValueError("uncertain-attempt resolver returned mismatched recovery evidence")
            records.append(record)
        async with self._lock:
            self._recoveries[(context.tenant_id, restore_id)] = list(records)
            now = datetime.now(UTC)
            for record in records:
                self._append_audit(
                    RecoveryAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=RecoveryAuditEventType.UNCERTAIN_ATTEMPT_RECONCILED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=str(record.attempt_id),
                        reason="reconcile uncertain execution after disaster recovery",
                        attributes={"disposition": record.disposition.value},
                    )
                )
        return tuple(records)

    async def record_recovery_gap(
        self,
        *,
        context: TenantAccessContext,
        restore_id: UUID,
        source_references: tuple[str, ...],
        unexpected_effect_count: int,
        unresolved_effect_count: int,
        evidence_reference: str,
    ) -> RecoveryGapReconciliation:
        async with self._lock:
            plan = self._plan_locked(context.tenant_id, restore_id)
            backup = self._backups[(context.tenant_id, plan.backup_id)]
        await self.use_break_glass(
            context=context,
            grant_id=plan.break_glass_grant_id,
            capability=BreakGlassCapability.RECONCILE,
            purpose=f"reconcile RPO recovery gap for {restore_id}",
        )
        now = datetime.now(UTC)
        record = RecoveryGapReconciliation(
            reconciliation_id=uuid4(),
            restore_id=restore_id,
            tenant_id=context.tenant_id,
            recovery_point_at=backup.recovery_point_at,
            failure_detected_at=plan.failure_detected_at,
            completed_at=now,
            source_references=source_references,
            unexpected_effect_count=unexpected_effect_count,
            unresolved_effect_count=unresolved_effect_count,
            evidence_reference=evidence_reference,
        )
        async with self._lock:
            key = (context.tenant_id, restore_id)
            existing = self._gaps.get(key)
            if existing is not None:
                if existing != record:
                    raise ValueError("recovery-gap reconciliation is immutable once recorded")
                return existing
            self._gaps[key] = record
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.RECOVERY_GAP_RECONCILED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(restore_id),
                    reason="reconcile effects across the backup RPO gap",
                    attributes={
                        "unexpected_effect_count": str(unexpected_effect_count),
                        "unresolved_effect_count": str(unresolved_effect_count),
                    },
                )
            )
            return record

    async def complete_restore(
        self,
        *,
        context: TenantAccessContext,
        restore_id: UUID,
        scan_id: UUID,
    ) -> RestoreResult:
        async with self._lock:
            plan = self._plan_locked(context.tenant_id, restore_id)
        await self.use_break_glass(
            context=context,
            grant_id=plan.break_glass_grant_id,
            capability=BreakGlassCapability.FAILOVER,
            purpose=f"authorize promotion of verified restore {restore_id}",
        )
        now = datetime.now(UTC)
        async with self._lock:
            backup = self._backups[(context.tenant_id, plan.backup_id)]
            profile = self._profile_locked(context.tenant_id, now)
            assert profile is not None
            scan = self._scans.get((context.tenant_id, scan_id))
            if scan is None or scan.restore_id != restore_id:
                raise ValueError("restore requires an integrity scan for the same restore")
            gap = self._gaps.get((context.tenant_id, restore_id))
            if gap is None:
                raise ValueError("restore requires reconciliation of the RPO recovery gap")
            recoveries = tuple(self._recoveries.get((context.tenant_id, restore_id), []))
            unresolved = any(
                record.disposition
                in {
                    UncertainRecoveryDisposition.RECONCILE_REQUIRED,
                    UncertainRecoveryDisposition.UNVERIFIABLE,
                }
                for record in recoveries
            )
            gap_reconciled = gap.unresolved_effect_count == 0
            actual_rpo = max(
                0.0,
                (plan.failure_detected_at - backup.recovery_point_at).total_seconds(),
            )
            actual_rto = max(0.0, (now - plan.failure_detected_at).total_seconds())
            integrity_ok = scan.status is RecoveryIntegrityStatus.VERIFIED
            targets_met = actual_rpo <= profile.rpo_seconds and actual_rto <= profile.rto_seconds
            if integrity_ok and not unresolved and gap_reconciled and targets_met:
                status = RestoreStatus.VERIFIED
                promoted = True
            elif integrity_ok:
                status = RestoreStatus.DEGRADED
                promoted = False
            else:
                status = RestoreStatus.FAILED
                promoted = False
            result = RestoreResult(
                restore_id=restore_id,
                tenant_id=context.tenant_id,
                backup_id=backup.backup_id,
                started_at=plan.failure_detected_at,
                completed_at=now,
                status=status,
                actual_rpo_seconds=actual_rpo,
                actual_rto_seconds=actual_rto,
                integrity_scan_id=scan_id,
                recovery_gap_reconciliation_id=gap.reconciliation_id,
                recovery_gap_reconciled=gap_reconciled,
                uncertain_recoveries=recoveries,
                promoted=promoted,
                completed_by=context.actor,
            )
            self._results[(context.tenant_id, restore_id)] = result
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.RESTORE_COMPLETED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(restore_id),
                    reason="complete disaster recovery restore",
                    attributes={
                        "status": status.value,
                        "actual_rpo_seconds": str(actual_rpo),
                        "actual_rto_seconds": str(actual_rto),
                        "recovery_gap_reconciled": str(gap_reconciled).lower(),
                    },
                )
            )
            return result

    async def record_game_day(
        self,
        *,
        context: TenantAccessContext,
        result: RestoreResult,
        simulated_site_failure: bool,
        notes: tuple[str, ...] = (),
    ) -> GameDayExercise:
        self._require_tenant(context, TenantCapability.CONFIGURE)
        if result.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("game-day result crossed tenant boundary")
        now = datetime.now(UTC)
        async with self._lock:
            profile = self._profile_locked(context.tenant_id, now)
            assert profile is not None
            scan = self._scans[(context.tenant_id, result.integrity_scan_id)]
            unresolved = any(
                record.disposition
                in {
                    UncertainRecoveryDisposition.RECONCILE_REQUIRED,
                    UncertainRecoveryDisposition.UNVERIFIABLE,
                }
                for record in result.uncertain_recoveries
            )
            passed = False
            exercise = GameDayExercise(
                exercise_id=uuid4(),
                tenant_id=context.tenant_id,
                profile_id=profile.profile_id,
                profile_revision=profile.revision,
                backup_id=result.backup_id,
                restore_id=result.restore_id,
                started_at=result.started_at,
                completed_at=now,
                simulated_site_failure=simulated_site_failure,
                achieved_rpo_seconds=result.actual_rpo_seconds,
                achieved_rto_seconds=result.actual_rto_seconds,
                chain_verified=scan.chain_verified,
                checkpoint_verified=scan.checkpoint_verified,
                trust_anchor_verified=scan.trust_anchor_verified,
                object_store_verified=scan.object_store_verified,
                uncertain_actions_reconciled=not unresolved,
                recovery_gap_reconciled=result.recovery_gap_reconciled,
                durable_catalog_verified=False,
                blind_replay_count=0,
                passed=passed,
                notes=notes,
            )
            self._game_days[(context.tenant_id, exercise.exercise_id)] = exercise
            self._append_audit(
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.GAME_DAY_RECORDED,
                    actor=context.actor,
                    occurred_at=exercise.completed_at,
                    target_id=str(exercise.exercise_id),
                    reason="record standalone disaster recovery exercise",
                    attributes={
                        "passed": "false",
                        "durable_catalog_verified": "false",
                    },
                )
            )
            return exercise

    async def audit_events(self, *, context: TenantAccessContext) -> tuple[RecoveryAuditEvent, ...]:
        self._require_tenant(context, TenantCapability.READ)
        async with self._lock:
            return tuple(self._audit.get(context.tenant_id, ()))

    def _profile_locked(
        self, tenant_id: str, at: datetime, *, required: bool = True
    ) -> ReliabilityProfile | None:
        active = [item for item in self._profiles.get(tenant_id, []) if item.effective_at <= at]
        profile = max(active, key=lambda item: item.revision) if active else None
        if profile is None and required:
            raise RuntimeError("reliability profile is not configured or not yet effective")
        return profile

    def _plan_locked(self, tenant_id: str, restore_id: UUID) -> RestorePlan:
        plan = self._plans.get((tenant_id, restore_id))
        if plan is None:
            raise KeyError(restore_id)
        return plan

    def _grant_locked(self, tenant_id: str, grant_id: UUID) -> BreakGlassGrant:
        grant = self._grants.get((tenant_id, grant_id))
        if grant is None:
            raise KeyError(grant_id)
        return grant

    def _append_audit(self, event: RecoveryAuditEvent) -> None:
        self._audit.setdefault(event.tenant_id, []).append(event)

    @staticmethod
    def _require(context: TenantAccessContext, capability: TenantCapability) -> None:
        if capability not in context.capabilities:
            raise AuthorizationDeniedError(f"tenant context lacks {capability.value} capability")

    @classmethod
    def _require_tenant(cls, context: TenantAccessContext, capability: TenantCapability) -> None:
        cls._require_tenant_mode(context)
        cls._require(context, capability)

    @staticmethod
    def _require_tenant_mode(context: TenantAccessContext) -> None:
        if context.mode is not TenantAccessMode.TENANT:
            raise AuthorizationDeniedError("support elevation is not disaster-recovery authority")
