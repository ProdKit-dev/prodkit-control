from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    RecoveryAuditEvent,
    RecoveryAuditEventType,
    RecoveryGapReconciliation,
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
)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("PostgreSQL did not return an aware database timestamp")
    return value


class RecoveryIntegrityVerifierPort(Protocol):
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
    ) -> IntegrityScanResult: ...


class UncertainAttemptResolver(Protocol):
    async def reconcile(
        self,
        *,
        attempt: ExecutionAttemptRecord,
        restore_id: UUID,
    ) -> UncertainExecutionRecovery: ...


class PostgresRecoveryStore:
    """Durable v0.7 recovery control boundary for the supported enterprise profile.

    Every recovery mutation is tenant-scoped. Support elevation is not recovery authority.
    Break-glass grants are independently approved, capability-scoped, short-lived, live-checked
    against revocation/expiry on each privileged use, and recorded with append-only audit evidence.
    Database time is authoritative for backup freshness, break-glass windows, RPO/RTO, and restore
    promotion. A verified promotion requires signed-checkpoint/trust-anchor integrity, reconciliation
    of every durable uncertain execution attempt, and explicit reconciliation of the RPO gap.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def publish_profile(
        self, *, context: TenantAccessContext, profile: ReliabilityProfile
    ) -> ReliabilityProfile:
        self._require_tenant(context, TenantCapability.CONFIGURE)
        if profile.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("reliability profile crossed tenant boundary")
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            if profile.created_at > now:
                raise ValueError("reliability profile creation time cannot be in the future")
            current_revision = await session.scalar(
                text(
                    "SELECT COALESCE(MAX(revision), 0) FROM recovery_profiles "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": context.tenant_id},
            )
            expected = int(current_revision or 0) + 1
            if profile.revision != expected:
                raise ValueError(f"reliability profile revision must advance to {expected}")
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_profiles (
                      tenant_id, profile_id, revision, effective_at, rpo_seconds, rto_seconds, document
                    ) VALUES (
                      :tenant_id, :profile_id, :revision, :effective_at, :rpo_seconds, :rto_seconds,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": profile.tenant_id,
                    "profile_id": profile.profile_id,
                    "revision": profile.revision,
                    "effective_at": profile.effective_at,
                    "rpo_seconds": profile.rpo_seconds,
                    "rto_seconds": profile.rto_seconds,
                    "document": profile.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.PROFILE_PUBLISHED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=profile.profile_id,
                    reason="publish reliability profile",
                    attributes={"revision": str(profile.revision)},
                ),
            )
        return profile

    async def current_profile(self, *, context: TenantAccessContext) -> ReliabilityProfile | None:
        self._require_tenant(context, TenantCapability.READ)
        async with self._sessions() as session:
            now = await _database_now(session)
            return await self._active_profile(session, context.tenant_id, now, required=False)

    async def record_backup(
        self, *, context: TenantAccessContext, manifest: BackupManifest
    ) -> BackupManifest:
        self._require_tenant(context, TenantCapability.WRITE)
        if manifest.tenant_id != context.tenant_id:
            raise AuthorizationDeniedError("backup manifest crossed tenant boundary")
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            if manifest.created_at > now:
                raise ValueError("backup manifest creation time cannot be in the future")
            profile = await self._active_profile(session, context.tenant_id, now)
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
            inserted = await session.scalar(
                text(
                    """
                    INSERT INTO recovery_backup_manifests (
                      tenant_id, backup_id, profile_id, profile_revision, source_schema_version,
                      recovery_point_at, created_at, trust_anchor_sha256, document
                    ) VALUES (
                      :tenant_id, :backup_id, :profile_id, :profile_revision, :source_schema_version,
                      :recovery_point_at, :created_at, :trust_anchor_sha256, CAST(:document AS JSONB)
                    )
                    ON CONFLICT (tenant_id, backup_id) DO NOTHING
                    RETURNING backup_id
                    """
                ),
                {
                    "tenant_id": manifest.tenant_id,
                    "backup_id": manifest.backup_id,
                    "profile_id": manifest.profile_id,
                    "profile_revision": manifest.profile_revision,
                    "source_schema_version": manifest.source_schema_version,
                    "recovery_point_at": manifest.recovery_point_at,
                    "created_at": manifest.created_at,
                    "trust_anchor_sha256": manifest.trust_anchor_sha256,
                    "document": manifest.model_dump_json(),
                },
            )
            existing = await self._backup(session, context.tenant_id, manifest.backup_id)
            if existing is None:
                raise RuntimeError("backup manifest disappeared during transaction")
            if existing != manifest:
                raise ValueError("backup manifest identity is immutable")
            if inserted is not None:
                await self._append_audit(
                    session,
                    RecoveryAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=RecoveryAuditEventType.BACKUP_RECORDED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=str(manifest.backup_id),
                        reason="record recovery backup",
                        attributes={"snapshot_set_id": manifest.snapshot_set_id},
                    ),
                )
        return manifest

    async def latest_usable_backup(self, *, context: TenantAccessContext) -> BackupManifest | None:
        self._require_tenant(context, TenantCapability.READ)
        async with self._sessions() as session:
            now = await _database_now(session)
            profile = await self._active_profile(session, context.tenant_id, now)
            assert profile is not None
            result = await session.execute(
                text(
                    """
                    SELECT document FROM recovery_backup_manifests
                    WHERE tenant_id = :tenant_id
                      AND profile_id = :profile_id
                      AND profile_revision = :profile_revision
                      AND recovery_point_at <= :now
                      AND recovery_point_at >= :minimum_point
                    ORDER BY recovery_point_at DESC, created_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": context.tenant_id,
                    "profile_id": profile.profile_id,
                    "profile_revision": profile.revision,
                    "now": now,
                    "minimum_point": now - timedelta(seconds=profile.max_backup_age_seconds),
                },
            )
            document = result.scalar_one_or_none()
            return BackupManifest.model_validate(document) if document is not None else None

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
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            profile = await self._active_profile(session, context.tenant_id, now)
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
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_break_glass_grants (
                      tenant_id, grant_id, operator_id, approved_by_id, issued_at, expires_at, document
                    ) VALUES (
                      :tenant_id, :grant_id, :operator_id, :approved_by_id, :issued_at, :expires_at,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": grant.tenant_id,
                    "grant_id": grant.grant_id,
                    "operator_id": grant.operator.id,
                    "approved_by_id": grant.approved_by.id,
                    "issued_at": grant.issued_at,
                    "expires_at": grant.expires_at,
                    "document": grant.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
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
                ),
            )
            return grant

    async def revoke_break_glass(
        self, *, context: TenantAccessContext, grant_id: UUID, reason: str
    ) -> None:
        self._require_tenant(context, TenantCapability.APPROVE)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            grant = await self._grant(session, context.tenant_id, grant_id)
            if grant is None:
                raise KeyError(grant_id)
            inserted = await session.scalar(
                text(
                    """
                    INSERT INTO recovery_break_glass_revocations (
                      revocation_id, tenant_id, grant_id, revoked_at, actor_id, reason
                    ) VALUES (
                      :revocation_id, :tenant_id, :grant_id, :revoked_at, :actor_id, :reason
                    )
                    ON CONFLICT (tenant_id, grant_id) DO NOTHING
                    RETURNING revocation_id
                    """
                ),
                {
                    "revocation_id": uuid4(),
                    "tenant_id": context.tenant_id,
                    "grant_id": grant_id,
                    "revoked_at": now,
                    "actor_id": context.actor.id,
                    "reason": reason,
                },
            )
            if inserted is not None:
                await self._append_audit(
                    session,
                    RecoveryAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=RecoveryAuditEventType.BREAK_GLASS_REVOKED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=str(grant_id),
                        reason=reason,
                        ticket_reference=grant.ticket_reference,
                    ),
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
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            return await self._record_break_glass_use(
                session,
                context=context,
                grant_id=grant_id,
                capability=capability,
                purpose=purpose,
                now=now,
            )

    async def plan_restore(
        self,
        *,
        context: TenantAccessContext,
        backup_id: UUID,
        target_site: str,
        grant_id: UUID,
    ) -> RestorePlan:
        self._require_tenant_mode(context)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            await self._record_break_glass_use(
                session,
                context=context,
                grant_id=grant_id,
                capability=BreakGlassCapability.RESTORE,
                purpose=f"restore backup {backup_id} to {target_site}",
                now=now,
            )
            backup = await self._backup(session, context.tenant_id, backup_id)
            if backup is None:
                raise KeyError(backup_id)
            profile = await self._active_profile(session, context.tenant_id, now)
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
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_restore_plans (
                      tenant_id, restore_id, backup_id, requested_at, target_site, document
                    ) VALUES (
                      :tenant_id, :restore_id, :backup_id, :requested_at, :target_site,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": plan.tenant_id,
                    "restore_id": plan.restore_id,
                    "backup_id": plan.backup_id,
                    "requested_at": plan.requested_at,
                    "target_site": plan.target_site,
                    "document": plan.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.RESTORE_PLANNED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(plan.restore_id),
                    reason="plan disaster recovery restore",
                    attributes={"target_site": target_site, "backup_id": str(backup_id)},
                ),
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
        verifier: RecoveryIntegrityVerifierPort,
    ) -> IntegrityScanResult:
        self._require_tenant_mode(context)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            plan = await self._plan(session, context.tenant_id, restore_id)
            if plan is None:
                raise KeyError(restore_id)
            await self._record_break_glass_use(
                session,
                context=context,
                grant_id=plan.break_glass_grant_id,
                capability=BreakGlassCapability.INTEGRITY_SCAN,
                purpose=f"verify restored assurance state for {restore_id}",
                now=now,
            )
            manifest = await self._backup(session, context.tenant_id, plan.backup_id)
            if manifest is None:
                raise RuntimeError("restore backup disappeared")
            scan = verifier.verify(
                manifest=manifest,
                restore_id=restore_id,
                observations=observations,
                ledger_chain_tip_sha256=ledger_chain_tip_sha256,
                checkpoint=checkpoint,
                trust_policy=trust_policy,
                completed_at=now,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_integrity_scans (
                      scan_id, tenant_id, restore_id, status, completed_at, document
                    ) VALUES (
                      :scan_id, :tenant_id, :restore_id, :status, :completed_at,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "scan_id": scan.scan_id,
                    "tenant_id": scan.tenant_id,
                    "restore_id": scan.restore_id,
                    "status": scan.status.value,
                    "completed_at": scan.completed_at,
                    "document": scan.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.INTEGRITY_SCAN_RECORDED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(scan.scan_id),
                    reason="verify post-restore assurance integrity",
                    attributes={"status": scan.status.value, "restore_id": str(restore_id)},
                ),
            )
            return scan

    async def reconcile_uncertain_attempts(
        self,
        *,
        context: TenantAccessContext,
        restore_id: UUID,
        resolver: UncertainAttemptResolver,
    ) -> tuple[UncertainExecutionRecovery, ...]:
        self._require_tenant_mode(context)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            plan = await self._plan(session, context.tenant_id, restore_id)
            if plan is None:
                raise KeyError(restore_id)
            await self._record_break_glass_use(
                session,
                context=context,
                grant_id=plan.break_glass_grant_id,
                capability=BreakGlassCapability.RECONCILE,
                purpose=f"begin uncertain execution reconciliation for {restore_id}",
                now=now,
            )
            rows = await session.execute(
                text(
                    """
                    SELECT document FROM execution_attempts
                    WHERE tenant_id = :tenant_id AND state = 'uncertain'
                    ORDER BY claimed_at, attempt_id
                    """
                ),
                {"tenant_id": context.tenant_id},
            )
            attempts = tuple(ExecutionAttemptRecord.model_validate(row[0]) for row in rows.all())

        records: list[UncertainExecutionRecovery] = []
        for attempt in attempts:
            if attempt.state is not ExecutionAttemptState.UNCERTAIN:
                raise RuntimeError("durable uncertain-attempt query returned non-uncertain state")
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

        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            plan = await self._plan(session, context.tenant_id, restore_id)
            if plan is None:
                raise KeyError(restore_id)
            await self._record_break_glass_use(
                session,
                context=context,
                grant_id=plan.break_glass_grant_id,
                capability=BreakGlassCapability.RECONCILE,
                purpose=f"commit uncertain execution reconciliation for {restore_id}",
                now=now,
            )
            current_rows = await session.execute(
                text(
                    "SELECT attempt_id FROM execution_attempts "
                    "WHERE tenant_id = :tenant_id AND state = 'uncertain'"
                ),
                {"tenant_id": context.tenant_id},
            )
            current_ids = {row[0] for row in current_rows.all()}
            if current_ids != {record.attempt_id for record in records}:
                raise ValueError(
                    "durable uncertain-attempt set changed during reconciliation; retry from observation"
                )
            for record in records:
                await session.execute(
                    text(
                        """
                        INSERT INTO recovery_uncertain_executions (
                          recovery_id, tenant_id, restore_id, attempt_id, action_id,
                          disposition, observed_at, document
                        ) VALUES (
                          :recovery_id, :tenant_id, :restore_id, :attempt_id, :action_id,
                          :disposition, :observed_at, CAST(:document AS JSONB)
                        )
                        """
                    ),
                    {
                        "recovery_id": record.recovery_id,
                        "tenant_id": record.tenant_id,
                        "restore_id": record.restore_id,
                        "attempt_id": record.attempt_id,
                        "action_id": record.action_id,
                        "disposition": record.disposition.value,
                        "observed_at": record.observed_at,
                        "document": record.model_dump_json(),
                    },
                )
                await self._append_audit(
                    session,
                    RecoveryAuditEvent(
                        event_id=uuid4(),
                        tenant_id=context.tenant_id,
                        event_type=RecoveryAuditEventType.UNCERTAIN_ATTEMPT_RECONCILED,
                        actor=context.actor,
                        occurred_at=now,
                        target_id=str(record.attempt_id),
                        reason="reconcile uncertain execution after disaster recovery",
                        attributes={"disposition": record.disposition.value},
                    ),
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
        self._require_tenant_mode(context)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            plan = await self._plan(session, context.tenant_id, restore_id)
            if plan is None:
                raise KeyError(restore_id)
            await self._record_break_glass_use(
                session,
                context=context,
                grant_id=plan.break_glass_grant_id,
                capability=BreakGlassCapability.RECONCILE,
                purpose=f"reconcile RPO recovery gap for {restore_id}",
                now=now,
            )
            backup = await self._backup(session, context.tenant_id, plan.backup_id)
            if backup is None:
                raise RuntimeError("restore backup disappeared")
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
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_gap_reconciliations (
                      reconciliation_id, tenant_id, restore_id, completed_at,
                      unresolved_effect_count, document
                    ) VALUES (
                      :reconciliation_id, :tenant_id, :restore_id, :completed_at,
                      :unresolved_effect_count, CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "reconciliation_id": record.reconciliation_id,
                    "tenant_id": record.tenant_id,
                    "restore_id": record.restore_id,
                    "completed_at": record.completed_at,
                    "unresolved_effect_count": record.unresolved_effect_count,
                    "document": record.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
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
                ),
            )
            return record

    async def complete_restore(
        self,
        *,
        context: TenantAccessContext,
        restore_id: UUID,
        scan_id: UUID,
    ) -> RestoreResult:
        self._require_tenant_mode(context)
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            plan = await self._plan(session, context.tenant_id, restore_id)
            if plan is None:
                raise KeyError(restore_id)
            await self._record_break_glass_use(
                session,
                context=context,
                grant_id=plan.break_glass_grant_id,
                capability=BreakGlassCapability.FAILOVER,
                purpose=f"authorize promotion of verified restore {restore_id}",
                now=now,
            )
            backup = await self._backup(session, context.tenant_id, plan.backup_id)
            if backup is None:
                raise RuntimeError("restore backup disappeared")
            profile = await self._active_profile(session, context.tenant_id, now)
            assert profile is not None
            if (plan.profile_id, plan.profile_revision) != (profile.profile_id, profile.revision):
                raise ValueError("reliability profile changed during restore; create a new plan")
            scan = await self._scan(session, context.tenant_id, scan_id)
            if scan is None or scan.restore_id != restore_id:
                raise ValueError("restore requires an integrity scan for the same restore")
            gap = await self._gap(session, context.tenant_id, restore_id)
            if gap is None:
                raise ValueError("restore requires reconciliation of the RPO recovery gap")
            recovery_rows = await session.execute(
                text(
                    """
                    SELECT document FROM recovery_uncertain_executions
                    WHERE tenant_id = :tenant_id AND restore_id = :restore_id
                    ORDER BY observed_at, recovery_id
                    """
                ),
                {"tenant_id": context.tenant_id, "restore_id": restore_id},
            )
            recoveries = tuple(
                UncertainExecutionRecovery.model_validate(row[0]) for row in recovery_rows.all()
            )
            uncertain_rows = await session.execute(
                text(
                    "SELECT attempt_id FROM execution_attempts "
                    "WHERE tenant_id = :tenant_id AND state = 'uncertain'"
                ),
                {"tenant_id": context.tenant_id},
            )
            authoritative_uncertain = {row[0] for row in uncertain_rows.all()}
            recovered_uncertain = {record.attempt_id for record in recoveries}
            unresolved = authoritative_uncertain != recovered_uncertain or any(
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
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_restore_results (
                      tenant_id, restore_id, backup_id, status, completed_at, document
                    ) VALUES (
                      :tenant_id, :restore_id, :backup_id, :status, :completed_at,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": result.tenant_id,
                    "restore_id": result.restore_id,
                    "backup_id": result.backup_id,
                    "status": result.status.value,
                    "completed_at": result.completed_at,
                    "document": result.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
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
                ),
            )
            return result

    async def get_restore_result(
        self, *, context: TenantAccessContext, restore_id: UUID
    ) -> RestoreResult | None:
        self._require_tenant(context, TenantCapability.READ)
        async with self._sessions() as session:
            document = await session.scalar(
                text(
                    "SELECT document FROM recovery_restore_results "
                    "WHERE tenant_id = :tenant_id AND restore_id = :restore_id"
                ),
                {"tenant_id": context.tenant_id, "restore_id": restore_id},
            )
            return RestoreResult.model_validate(document) if document is not None else None

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
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            profile = await self._active_profile(session, context.tenant_id, now)
            assert profile is not None
            scan = await self._scan(session, context.tenant_id, result.integrity_scan_id)
            if scan is None:
                raise ValueError("game day requires durable integrity-scan evidence")
            unresolved = any(
                record.disposition
                in {
                    UncertainRecoveryDisposition.RECONCILE_REQUIRED,
                    UncertainRecoveryDisposition.UNVERIFIABLE,
                }
                for record in result.uncertain_recoveries
            )
            passed = (
                simulated_site_failure
                and result.status is RestoreStatus.VERIFIED
                and result.promoted
                and result.actual_rpo_seconds <= profile.rpo_seconds
                and result.actual_rto_seconds <= profile.rto_seconds
                and scan.chain_verified
                and scan.checkpoint_verified
                and scan.trust_anchor_verified
                and scan.object_store_verified
                and not unresolved
                and result.recovery_gap_reconciled
            )
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
                durable_catalog_verified=True,
                blind_replay_count=0,
                passed=passed,
                notes=notes,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_game_day_exercises (
                      exercise_id, tenant_id, restore_id, backup_id, passed, completed_at, document
                    ) VALUES (
                      :exercise_id, :tenant_id, :restore_id, :backup_id, :passed, :completed_at,
                      CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "exercise_id": exercise.exercise_id,
                    "tenant_id": exercise.tenant_id,
                    "restore_id": exercise.restore_id,
                    "backup_id": exercise.backup_id,
                    "passed": exercise.passed,
                    "completed_at": exercise.completed_at,
                    "document": exercise.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                RecoveryAuditEvent(
                    event_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=RecoveryAuditEventType.GAME_DAY_RECORDED,
                    actor=context.actor,
                    occurred_at=now,
                    target_id=str(exercise.exercise_id),
                    reason="record durable disaster recovery game day",
                    attributes={
                        "passed": str(exercise.passed).lower(),
                        "durable_catalog_verified": "true",
                    },
                ),
            )
            return exercise

    async def get_game_day(
        self, *, context: TenantAccessContext, exercise_id: UUID
    ) -> GameDayExercise | None:
        self._require_tenant(context, TenantCapability.READ)
        async with self._sessions() as session:
            document = await session.scalar(
                text(
                    "SELECT document FROM recovery_game_day_exercises "
                    "WHERE tenant_id = :tenant_id AND exercise_id = :exercise_id"
                ),
                {"tenant_id": context.tenant_id, "exercise_id": exercise_id},
            )
            return GameDayExercise.model_validate(document) if document is not None else None

    async def audit_events(self, *, context: TenantAccessContext) -> tuple[RecoveryAuditEvent, ...]:
        self._require_tenant(context, TenantCapability.READ)
        async with self._sessions() as session:
            result = await session.execute(
                text(
                    "SELECT document FROM recovery_audit_events "
                    "WHERE tenant_id = :tenant_id ORDER BY occurred_at, event_id"
                ),
                {"tenant_id": context.tenant_id},
            )
            return tuple(RecoveryAuditEvent.model_validate(row[0]) for row in result.all())

    async def _record_break_glass_use(
        self,
        session: AsyncSession,
        *,
        context: TenantAccessContext,
        grant_id: UUID,
        capability: BreakGlassCapability,
        purpose: str,
        now: datetime,
    ) -> BreakGlassUse:
        self._require_tenant_mode(context)
        grant = await self._grant(session, context.tenant_id, grant_id, for_share=True)
        if grant is None:
            raise KeyError(grant_id)
        revoked = await session.scalar(
            text(
                "SELECT 1 FROM recovery_break_glass_revocations "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id"
            ),
            {"tenant_id": context.tenant_id, "grant_id": grant_id},
        )
        if revoked is not None:
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
        await session.execute(
            text(
                """
                INSERT INTO recovery_break_glass_uses (
                  use_id, tenant_id, grant_id, capability, occurred_at, document
                ) VALUES (
                  :use_id, :tenant_id, :grant_id, :capability, :occurred_at,
                  CAST(:document AS JSONB)
                )
                """
            ),
            {
                "use_id": use.use_id,
                "tenant_id": use.tenant_id,
                "grant_id": use.grant_id,
                "capability": use.capability.value,
                "occurred_at": use.occurred_at,
                "document": use.model_dump_json(),
            },
        )
        await self._append_audit(
            session,
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
            ),
        )
        return use

    @staticmethod
    async def _append_audit(session: AsyncSession, event: RecoveryAuditEvent) -> None:
        await session.execute(
            text(
                """
                INSERT INTO recovery_audit_events (
                  event_id, tenant_id, event_type, occurred_at, target_id, document
                ) VALUES (
                  :event_id, :tenant_id, :event_type, :occurred_at, :target_id,
                  CAST(:document AS JSONB)
                )
                """
            ),
            {
                "event_id": event.event_id,
                "tenant_id": event.tenant_id,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at,
                "target_id": event.target_id,
                "document": event.model_dump_json(),
            },
        )

    @staticmethod
    async def _active_profile(
        session: AsyncSession,
        tenant_id: str,
        at: datetime,
        *,
        required: bool = True,
    ) -> ReliabilityProfile | None:
        document = await session.scalar(
            text(
                """
                SELECT document FROM recovery_profiles
                WHERE tenant_id = :tenant_id AND effective_at <= :at
                ORDER BY revision DESC LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "at": at},
        )
        profile = ReliabilityProfile.model_validate(document) if document is not None else None
        if profile is None and required:
            raise RuntimeError("reliability profile is not configured or not yet effective")
        return profile

    @staticmethod
    async def _backup(
        session: AsyncSession, tenant_id: str, backup_id: UUID
    ) -> BackupManifest | None:
        document = await session.scalar(
            text(
                "SELECT document FROM recovery_backup_manifests "
                "WHERE tenant_id = :tenant_id AND backup_id = :backup_id"
            ),
            {"tenant_id": tenant_id, "backup_id": backup_id},
        )
        return BackupManifest.model_validate(document) if document is not None else None

    @staticmethod
    async def _plan(session: AsyncSession, tenant_id: str, restore_id: UUID) -> RestorePlan | None:
        document = await session.scalar(
            text(
                "SELECT document FROM recovery_restore_plans "
                "WHERE tenant_id = :tenant_id AND restore_id = :restore_id"
            ),
            {"tenant_id": tenant_id, "restore_id": restore_id},
        )
        return RestorePlan.model_validate(document) if document is not None else None

    @staticmethod
    async def _scan(
        session: AsyncSession, tenant_id: str, scan_id: UUID
    ) -> IntegrityScanResult | None:
        document = await session.scalar(
            text(
                "SELECT document FROM recovery_integrity_scans "
                "WHERE tenant_id = :tenant_id AND scan_id = :scan_id"
            ),
            {"tenant_id": tenant_id, "scan_id": scan_id},
        )
        return IntegrityScanResult.model_validate(document) if document is not None else None

    @staticmethod
    async def _gap(
        session: AsyncSession, tenant_id: str, restore_id: UUID
    ) -> RecoveryGapReconciliation | None:
        document = await session.scalar(
            text(
                "SELECT document FROM recovery_gap_reconciliations "
                "WHERE tenant_id = :tenant_id AND restore_id = :restore_id"
            ),
            {"tenant_id": tenant_id, "restore_id": restore_id},
        )
        return RecoveryGapReconciliation.model_validate(document) if document is not None else None

    @staticmethod
    async def _grant(
        session: AsyncSession,
        tenant_id: str,
        grant_id: UUID,
        *,
        for_share: bool = False,
    ) -> BreakGlassGrant | None:
        statement = (
            text(
                "SELECT document FROM recovery_break_glass_grants "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id FOR SHARE"
            )
            if for_share
            else text(
                "SELECT document FROM recovery_break_glass_grants "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id"
            )
        )
        document = await session.scalar(
            statement,
            {"tenant_id": tenant_id, "grant_id": grant_id},
        )
        return BreakGlassGrant.model_validate(document) if document is not None else None

    @staticmethod
    async def _tenant_lock(session: AsyncSession, tenant_id: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant_id, 7070))"),
            {"tenant_id": tenant_id},
        )

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
