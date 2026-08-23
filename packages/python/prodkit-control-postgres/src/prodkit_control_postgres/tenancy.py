from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    ActorRef,
    AuthorizationDeniedError,
    SupportElevationGrant,
    TenantAccessContext,
    TenantAccessMode,
    TenantAuditEvent,
    TenantAuditEventType,
    TenantCapability,
    TenantExportManifest,
    TenantIsolationProfile,
    TenantLifecycleRecord,
    TenantLifecycleStatus,
    sha256_hex,
)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("PostgreSQL did not return an aware database timestamp")
    return value


class PostgresTenantControlStore:
    """Durable tenant isolation, support-elevation, lifecycle, export, and audit control plane."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def put_profile(
        self,
        profile: TenantIsolationProfile,
        *,
        context: TenantAccessContext,
    ) -> None:
        if context.mode is not TenantAccessMode.TENANT:
            raise AuthorizationDeniedError(
                "support elevation cannot change tenant isolation policy"
            )
        self._require_context(context, TenantCapability.CONFIGURE)
        if context.tenant_id != profile.tenant_id:
            raise AuthorizationDeniedError("tenant profile mutation crossed tenant boundary")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await session.execute(
                text(
                    """
                    INSERT INTO tenant_isolation_profiles (tenant_id, updated_at, document)
                    VALUES (:tenant_id, :updated_at, CAST(:document AS JSONB))
                    ON CONFLICT (tenant_id) DO UPDATE SET
                      updated_at = EXCLUDED.updated_at,
                      document = EXCLUDED.document
                    """
                ),
                {
                    "tenant_id": profile.tenant_id,
                    "updated_at": now,
                    "document": profile.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=profile.tenant_id,
                    event_type=TenantAuditEventType.CONFIGURATION_CHANGED,
                    actor=context.actor,
                    occurred_at=now,
                    attributes={"profile_digest": sha256_hex(profile)},
                ),
            )

    async def get_profile(
        self,
        *,
        context: TenantAccessContext,
    ) -> TenantIsolationProfile | None:
        async with self._sessions() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            return await self._profile(session, context.tenant_id)

    async def issue_support_grant(
        self,
        *,
        target_tenant_id: str,
        operator: ActorRef,
        issued_by: ActorRef,
        capabilities: tuple[TenantCapability, ...],
        reason: str,
        ticket_reference: str,
        ttl_seconds: int = 900,
    ) -> SupportElevationGrant:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise ValueError("support elevation TTL must be between 30 and 3600 seconds")
        self._require_support_authority(issued_by)
        self._require_support_operator(operator)
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            profile = await self._profile(session, target_tenant_id, for_update=True)
            if profile is None or not profile.allow_support_access:
                raise AuthorizationDeniedError("target tenant has not enabled support elevation")
            grant = SupportElevationGrant(
                grant_id=uuid4(),
                target_tenant_id=target_tenant_id,
                operator=operator,
                issued_by=issued_by,
                capabilities=capabilities,
                reason=reason,
                ticket_reference=ticket_reference,
                issued_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            await session.execute(
                text(
                    """
                    INSERT INTO support_elevation_grants (
                      tenant_id, grant_id, operator_identity, issued_at, expires_at,
                      revoked_at, document
                    ) VALUES (
                      :tenant_id, :grant_id, :operator_identity, :issued_at, :expires_at,
                      NULL, CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "tenant_id": target_tenant_id,
                    "grant_id": grant.grant_id,
                    "operator_identity": self._actor_identity(operator),
                    "issued_at": grant.issued_at,
                    "expires_at": grant.expires_at,
                    "document": grant.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_ISSUED,
                    actor=issued_by,
                    occurred_at=now,
                    elevation_id=grant.grant_id,
                    reason=reason,
                    ticket_reference=ticket_reference,
                    attributes={
                        "operator_id": operator.id,
                        "capabilities": ",".join(cap.value for cap in capabilities),
                    },
                ),
            )
            return grant

    async def redeem_support_grant(
        self,
        *,
        target_tenant_id: str,
        grant_id: UUID,
        operator: ActorRef,
    ) -> TenantAccessContext:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            profile = await self._profile(session, target_tenant_id)
            grant = await self._grant(session, target_tenant_id, grant_id, for_update=True)
            if profile is None or not profile.allow_support_access:
                raise AuthorizationDeniedError("target tenant has disabled support elevation")
            if (
                grant is None
                or not self._same_actor_identity(grant.operator, operator)
                or not grant.active(at=now)
            ):
                raise AuthorizationDeniedError("support elevation is missing, expired, or revoked")
            context = TenantAccessContext(
                tenant_id=target_tenant_id,
                actor=operator,
                mode=TenantAccessMode.SUPPORT,
                capabilities=grant.capabilities,
                elevation_id=grant.grant_id,
                reason=grant.reason,
                ticket_reference=grant.ticket_reference,
                issued_at=grant.issued_at,
                expires_at=grant.expires_at,
            )
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_USED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    elevation_id=grant.grant_id,
                    reason=grant.reason,
                    ticket_reference=grant.ticket_reference,
                ),
            )
            return context

    async def revoke_support_grant(
        self,
        *,
        target_tenant_id: str,
        grant_id: UUID,
        actor: ActorRef,
        reason: str,
    ) -> None:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            grant = await self._grant(session, target_tenant_id, grant_id, for_update=True)
            if grant is None:
                raise KeyError(grant_id)
            if not self._same_actor_identity(grant.operator, actor):
                self._require_support_authority(actor)
            if grant.revoked_at is not None:
                return
            revoked = grant.model_copy(update={"revoked_at": now})
            await session.execute(
                text(
                    """
                    UPDATE support_elevation_grants
                    SET revoked_at = :revoked_at, document = CAST(:document AS JSONB)
                    WHERE tenant_id = :tenant_id AND grant_id = :grant_id
                    """
                ),
                {
                    "tenant_id": target_tenant_id,
                    "grant_id": grant_id,
                    "revoked_at": now,
                    "document": revoked.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_REVOKED,
                    actor=actor,
                    occurred_at=now,
                    elevation_id=grant_id,
                    reason=reason,
                    ticket_reference=grant.ticket_reference,
                ),
            )

    async def set_legal_hold(
        self,
        *,
        context: TenantAccessContext,
        enabled: bool,
        reason: str,
    ) -> TenantLifecycleRecord:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.LEGAL_HOLD, now=now)
            current = await self._lifecycle(session, context.tenant_id, for_update=True)
            status = current.status if current is not None else TenantLifecycleStatus.ACTIVE
            if enabled and status is TenantLifecycleStatus.DELETION_SCHEDULED:
                status = TenantLifecycleStatus.ACTIVE
            record = TenantLifecycleRecord(
                tenant_id=context.tenant_id,
                status=status,
                legal_hold=enabled,
                updated_at=now,
                updated_by=context.audited_actor(),
                elevation_id=context.elevation_id,
            )
            await self._save_lifecycle(session, record)
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=(
                        TenantAuditEventType.LEGAL_HOLD_PLACED
                        if enabled
                        else TenantAuditEventType.LEGAL_HOLD_RELEASED
                    ),
                    actor=context.audited_actor(),
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=reason,
                    ticket_reference=context.ticket_reference,
                ),
            )
            return record

    async def schedule_deletion(
        self,
        *,
        context: TenantAccessContext,
        not_before: datetime,
        reason: str,
    ) -> TenantLifecycleRecord:
        if not_before.tzinfo is None or not_before.utcoffset() is None:
            raise ValueError("tenant deletion requires an aware datetime")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            if not_before <= now:
                raise ValueError("tenant deletion must be scheduled for a future aware datetime")
            await self._authorize(session, context, TenantCapability.DELETE, now=now)
            current = await self._lifecycle(session, context.tenant_id, for_update=True)
            if current is not None and current.legal_hold:
                raise AuthorizationDeniedError("tenant deletion is blocked by legal hold")
            record = TenantLifecycleRecord(
                tenant_id=context.tenant_id,
                status=TenantLifecycleStatus.DELETION_SCHEDULED,
                legal_hold=False,
                deletion_not_before=not_before,
                updated_at=now,
                updated_by=context.audited_actor(),
                elevation_id=context.elevation_id,
            )
            await self._save_lifecycle(session, record)
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=TenantAuditEventType.DELETION_SCHEDULED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=reason,
                    ticket_reference=context.ticket_reference,
                    attributes={"not_before": not_before.isoformat()},
                ),
            )
            return record

    async def complete_deletion(
        self,
        *,
        context: TenantAccessContext,
    ) -> TenantLifecycleRecord:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.DELETE, now=now)
            current = await self._lifecycle(session, context.tenant_id, for_update=True)
            if (
                current is None
                or current.status is not TenantLifecycleStatus.DELETION_SCHEDULED
                or current.deletion_not_before is None
                or now < current.deletion_not_before
                or current.legal_hold
            ):
                raise AuthorizationDeniedError("tenant deletion is not currently permitted")
            deleted = current.model_copy(
                update={
                    "status": TenantLifecycleStatus.DELETED,
                    "deletion_not_before": None,
                    "updated_at": now,
                    "updated_by": context.audited_actor(),
                    "elevation_id": context.elevation_id,
                }
            )
            await self._save_lifecycle(session, deleted)
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=TenantAuditEventType.DELETION_COMPLETED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=context.reason,
                    ticket_reference=context.ticket_reference,
                ),
            )
            return deleted

    async def get_lifecycle(
        self,
        *,
        context: TenantAccessContext,
    ) -> TenantLifecycleRecord | None:
        async with self._sessions() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            return await self._lifecycle(session, context.tenant_id)

    async def export_manifest(
        self,
        *,
        context: TenantAccessContext,
        record_counts: dict[str, int],
        content_digests: tuple[str, ...] = (),
    ) -> TenantExportManifest:
        if any(value < 0 for value in record_counts.values()):
            raise ValueError("tenant export record counts cannot be negative")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.EXPORT, now=now)
            manifest = TenantExportManifest(
                export_id=uuid4(),
                tenant_id=context.tenant_id,
                created_at=now,
                created_by=context.audited_actor(),
                elevation_id=context.elevation_id,
                record_counts=record_counts,
                content_digests=content_digests,
                legal_hold_preserved=True,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO tenant_export_manifests (
                      export_id, tenant_id, created_at, document
                    ) VALUES (
                      :export_id, :tenant_id, :created_at, CAST(:document AS JSONB)
                    )
                    """
                ),
                {
                    "export_id": manifest.export_id,
                    "tenant_id": manifest.tenant_id,
                    "created_at": manifest.created_at,
                    "document": manifest.model_dump_json(),
                },
            )
            await self._append_audit(
                session,
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=TenantAuditEventType.EXPORT_CREATED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=context.reason,
                    ticket_reference=context.ticket_reference,
                    attributes={"manifest_digest": sha256_hex(manifest)},
                ),
            )
            return manifest

    async def list_audit(
        self,
        *,
        context: TenantAccessContext,
    ) -> tuple[TenantAuditEvent, ...]:
        async with self._sessions() as session:
            now = await _database_now(session)
            await self._authorize(session, context, TenantCapability.READ, now=now)
            rows = (
                (
                    await session.execute(
                        text(
                            """
                            SELECT document
                            FROM tenant_audit_events
                            WHERE tenant_id = :tenant_id
                            ORDER BY occurred_at, audit_id
                            """
                        ),
                        {"tenant_id": context.tenant_id},
                    )
                )
                .mappings()
                .all()
            )
            return tuple(TenantAuditEvent.model_validate(row["document"]) for row in rows)

    async def _authorize(
        self,
        session: AsyncSession,
        context: TenantAccessContext,
        capability: TenantCapability,
        *,
        now: datetime,
    ) -> None:
        self._require_context(context, capability, at=now)
        if context.mode is TenantAccessMode.TENANT:
            return
        if context.elevation_id is None:
            raise AuthorizationDeniedError("support access has no elevation identity")
        profile = await self._profile(session, context.tenant_id)
        grant = await self._grant(session, context.tenant_id, context.elevation_id)
        if profile is None or not profile.allow_support_access:
            raise AuthorizationDeniedError("target tenant has disabled support elevation")
        if (
            grant is None
            or not grant.active(at=now)
            or not self._same_actor_identity(grant.operator, context.actor)
            or capability not in grant.capabilities
            or grant.reason != context.reason
            or grant.ticket_reference != context.ticket_reference
        ):
            raise AuthorizationDeniedError("support elevation no longer authorizes this operation")

    @staticmethod
    def _require_context(
        context: TenantAccessContext,
        capability: TenantCapability,
        *,
        at: datetime | None = None,
    ) -> None:
        try:
            context.require(capability, at=at or datetime.now(UTC))
        except PermissionError as exc:
            raise AuthorizationDeniedError(str(exc)) from exc

    @staticmethod
    def _same_actor_identity(left: ActorRef, right: ActorRef) -> bool:
        return (
            left.kind == right.kind
            and left.id == right.id
            and left.tenant_id == right.tenant_id
            and left.workload_identity == right.workload_identity
        )

    @classmethod
    def _actor_identity(cls, actor: ActorRef) -> str:
        return "|".join(
            (
                actor.kind.value,
                actor.tenant_id,
                actor.id,
                actor.workload_identity or "",
            )
        )

    @staticmethod
    def _require_support_authority(actor: ActorRef) -> None:
        if actor.attributes.get("prodkit.support_authority") != "true":
            raise AuthorizationDeniedError("support elevation issuer lacks support authority")

    @staticmethod
    def _require_support_operator(actor: ActorRef) -> None:
        if actor.attributes.get("prodkit.support_operator") != "true":
            raise AuthorizationDeniedError("support elevation recipient is not a support operator")

    async def _profile(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        for_update: bool = False,
    ) -> TenantIsolationProfile | None:
        statement = (
            text(
                "SELECT document FROM tenant_isolation_profiles "
                "WHERE tenant_id = :tenant_id FOR UPDATE"
            )
            if for_update
            else text("SELECT document FROM tenant_isolation_profiles WHERE tenant_id = :tenant_id")
        )
        row = (await session.execute(statement, {"tenant_id": tenant_id})).mappings().first()
        return TenantIsolationProfile.model_validate(row["document"]) if row is not None else None

    async def _grant(
        self,
        session: AsyncSession,
        tenant_id: str,
        grant_id: UUID,
        *,
        for_update: bool = False,
    ) -> SupportElevationGrant | None:
        statement = (
            text(
                "SELECT document FROM support_elevation_grants "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id FOR UPDATE"
            )
            if for_update
            else text(
                "SELECT document FROM support_elevation_grants "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id"
            )
        )
        row = (
            (
                await session.execute(
                    statement,
                    {"tenant_id": tenant_id, "grant_id": grant_id},
                )
            )
            .mappings()
            .first()
        )
        return SupportElevationGrant.model_validate(row["document"]) if row is not None else None

    async def _lifecycle(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        for_update: bool = False,
    ) -> TenantLifecycleRecord | None:
        statement = (
            text("SELECT document FROM tenant_lifecycle WHERE tenant_id = :tenant_id FOR UPDATE")
            if for_update
            else text("SELECT document FROM tenant_lifecycle WHERE tenant_id = :tenant_id")
        )
        row = (await session.execute(statement, {"tenant_id": tenant_id})).mappings().first()
        return TenantLifecycleRecord.model_validate(row["document"]) if row is not None else None

    async def _save_lifecycle(
        self,
        session: AsyncSession,
        record: TenantLifecycleRecord,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO tenant_lifecycle (
                  tenant_id, status, legal_hold, deletion_not_before, updated_at, document
                ) VALUES (
                  :tenant_id, :status, :legal_hold, :deletion_not_before, :updated_at,
                  CAST(:document AS JSONB)
                )
                ON CONFLICT (tenant_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  legal_hold = EXCLUDED.legal_hold,
                  deletion_not_before = EXCLUDED.deletion_not_before,
                  updated_at = EXCLUDED.updated_at,
                  document = EXCLUDED.document
                """
            ),
            {
                "tenant_id": record.tenant_id,
                "status": record.status.value,
                "legal_hold": record.legal_hold,
                "deletion_not_before": record.deletion_not_before,
                "updated_at": record.updated_at,
                "document": record.model_dump_json(),
            },
        )

    @staticmethod
    async def _append_audit(session: AsyncSession, event: TenantAuditEvent) -> None:
        await session.execute(
            text(
                """
                INSERT INTO tenant_audit_events (
                  audit_id, tenant_id, event_type, occurred_at, elevation_id, document
                ) VALUES (
                  :audit_id, :tenant_id, :event_type, :occurred_at, :elevation_id,
                  CAST(:document AS JSONB)
                )
                """
            ),
            {
                "audit_id": event.audit_id,
                "tenant_id": event.tenant_id,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at,
                "elevation_id": event.elevation_id,
                "document": event.model_dump_json(),
            },
        )
