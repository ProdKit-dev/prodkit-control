from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    AuthorizationDeniedError,
    BackupManifest,
    BreakGlassCapability,
    BreakGlassGrant,
    BreakGlassUse,
    GameDayExercise,
    IntegrityScanResult,
    RecoveryAuditEvent,
    ReliabilityProfile,
    RestorePlan,
    RestoreResult,
    TenantAccessContext,
    UncertainExecutionRecovery,
)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("PostgreSQL did not return an aware database timestamp")
    return value


class PostgresRecoveryStore:
    """Durable, tenant-scoped v0.7 recovery evidence repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def save_profile(self, profile: ReliabilityProfile) -> ReliabilityProfile:
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, profile.tenant_id)
            current_revision = await session.scalar(
                text("SELECT COALESCE(MAX(revision), 0) FROM recovery_profiles WHERE tenant_id = :tenant_id"),
                {"tenant_id": profile.tenant_id},
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
        return profile

    async def current_profile(self, tenant_id: str) -> ReliabilityProfile | None:
        async with self._sessions() as session:
            document = await session.scalar(
                text(
                    """
                    SELECT document FROM recovery_profiles
                    WHERE tenant_id = :tenant_id
                    ORDER BY revision DESC LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id},
            )
            return ReliabilityProfile.model_validate(document) if document is not None else None

    async def save_backup(self, manifest: BackupManifest) -> BackupManifest:
        async with self._sessions.begin() as session:
            await session.execute(
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
            document = await session.scalar(
                text(
                    "SELECT document FROM recovery_backup_manifests "
                    "WHERE tenant_id = :tenant_id AND backup_id = :backup_id"
                ),
                {"tenant_id": manifest.tenant_id, "backup_id": manifest.backup_id},
            )
            if document is None:
                raise RuntimeError("backup manifest disappeared during transaction")
            existing = BackupManifest.model_validate(document)
            if existing != manifest:
                raise ValueError("backup manifest identity is immutable")
        return manifest

    async def get_backup(self, tenant_id: str, backup_id: UUID) -> BackupManifest | None:
        async with self._sessions() as session:
            document = await session.scalar(
                text(
                    "SELECT document FROM recovery_backup_manifests "
                    "WHERE tenant_id = :tenant_id AND backup_id = :backup_id"
                ),
                {"tenant_id": tenant_id, "backup_id": backup_id},
            )
            return BackupManifest.model_validate(document) if document is not None else None

    async def save_break_glass_grant(self, grant: BreakGlassGrant) -> BreakGlassGrant:
        async with self._sessions.begin() as session:
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
        return grant

    async def use_break_glass(
        self,
        *,
        context: TenantAccessContext,
        grant_id: UUID,
        capability: BreakGlassCapability,
        purpose: str,
    ) -> BreakGlassUse:
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            document = await session.scalar(
                text(
                    """
                    SELECT document FROM recovery_break_glass_grants
                    WHERE tenant_id = :tenant_id AND grant_id = :grant_id
                    FOR SHARE
                    """
                ),
                {"tenant_id": context.tenant_id, "grant_id": grant_id},
            )
            if document is None:
                raise KeyError(grant_id)
            grant = BreakGlassGrant.model_validate(document)
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
            return use

    async def revoke_break_glass(
        self,
        *,
        context: TenantAccessContext,
        grant_id: UUID,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            await self._tenant_lock(session, context.tenant_id)
            now = await _database_now(session)
            exists = await session.scalar(
                text(
                    "SELECT 1 FROM recovery_break_glass_grants "
                    "WHERE tenant_id = :tenant_id AND grant_id = :grant_id"
                ),
                {"tenant_id": context.tenant_id, "grant_id": grant_id},
            )
            if exists is None:
                raise KeyError(grant_id)
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_break_glass_revocations (
                      revocation_id, tenant_id, grant_id, revoked_at, actor_id, reason
                    ) VALUES (
                      :revocation_id, :tenant_id, :grant_id, :revoked_at, :actor_id, :reason
                    )
                    ON CONFLICT (tenant_id, grant_id) DO NOTHING
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

    async def save_restore_plan(self, plan: RestorePlan) -> RestorePlan:
        async with self._sessions.begin() as session:
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
        return plan

    async def get_restore_plan(self, tenant_id: str, restore_id: UUID) -> RestorePlan | None:
        async with self._sessions() as session:
            document = await session.scalar(
                text(
                    "SELECT document FROM recovery_restore_plans "
                    "WHERE tenant_id = :tenant_id AND restore_id = :restore_id"
                ),
                {"tenant_id": tenant_id, "restore_id": restore_id},
            )
            return RestorePlan.model_validate(document) if document is not None else None

    async def save_integrity_scan(self, scan: IntegrityScanResult) -> IntegrityScanResult:
        async with self._sessions.begin() as session:
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
        return scan

    async def save_uncertain_recovery(
        self, record: UncertainExecutionRecovery
    ) -> UncertainExecutionRecovery:
        async with self._sessions.begin() as session:
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
        return record

    async def list_uncertain_recoveries(
        self, tenant_id: str, restore_id: UUID
    ) -> tuple[UncertainExecutionRecovery, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                text(
                    """
                    SELECT document FROM recovery_uncertain_executions
                    WHERE tenant_id = :tenant_id AND restore_id = :restore_id
                    ORDER BY observed_at, recovery_id
                    """
                ),
                {"tenant_id": tenant_id, "restore_id": restore_id},
            )
            return tuple(UncertainExecutionRecovery.model_validate(row[0]) for row in result.all())

    async def save_restore_result(self, result: RestoreResult) -> RestoreResult:
        async with self._sessions.begin() as session:
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
        return result

    async def save_game_day(self, exercise: GameDayExercise) -> GameDayExercise:
        async with self._sessions.begin() as session:
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
        return exercise

    async def append_audit(self, event: RecoveryAuditEvent) -> RecoveryAuditEvent:
        async with self._sessions.begin() as session:
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
        return event

    async def list_audit(self, tenant_id: str) -> tuple[RecoveryAuditEvent, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                text(
                    "SELECT document FROM recovery_audit_events "
                    "WHERE tenant_id = :tenant_id ORDER BY occurred_at, event_id"
                ),
                {"tenant_id": tenant_id},
            )
            return tuple(RecoveryAuditEvent.model_validate(row[0]) for row in result.all())

    @staticmethod
    async def _tenant_lock(session: AsyncSession, tenant_id: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant_id, 7070))"),
            {"tenant_id": tenant_id},
        )
