from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

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


class InMemoryTenantControlStore:
    """Standalone tenant configuration, elevation, lifecycle, and audit control plane.

    Every tenant-data operation requires an explicit access context. Support-mode contexts are
    revalidated against the live grant and the tenant's current opt-in on every use, so grant
    revocation, expiry, or tenant opt-out takes effect immediately. Production adapters can
    persist the same canonical records in a database or dedicated governance service.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, TenantIsolationProfile] = {}
        self._grants: dict[tuple[str, UUID], SupportElevationGrant] = {}
        self._lifecycle: dict[str, TenantLifecycleRecord] = {}
        self._audit: dict[str, list[TenantAuditEvent]] = {}
        self._lock = asyncio.Lock()

    async def put_profile(
        self,
        profile: TenantIsolationProfile,
        *,
        context: TenantAccessContext,
    ) -> None:
        if context.mode is not TenantAccessMode.TENANT:
            raise AuthorizationDeniedError("support elevation cannot change tenant isolation policy")
        context.require(TenantCapability.CONFIGURE)
        if context.tenant_id != profile.tenant_id:
            raise AuthorizationDeniedError("tenant profile mutation crossed tenant boundary")
        now = datetime.now(UTC)
        async with self._lock:
            self._profiles[profile.tenant_id] = profile
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=profile.tenant_id,
                    event_type=TenantAuditEventType.CONFIGURATION_CHANGED,
                    actor=context.actor,
                    occurred_at=now,
                    attributes={"profile_digest": sha256_hex(profile)},
                )
            )

    async def get_profile(
        self,
        *,
        context: TenantAccessContext,
    ) -> TenantIsolationProfile | None:
        async with self._lock:
            self._authorize_locked(context, TenantCapability.READ)
            return self._profiles.get(context.tenant_id)

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
        now = datetime.now(UTC)
        async with self._lock:
            profile = self._profiles.get(target_tenant_id)
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
            self._grants[(target_tenant_id, grant.grant_id)] = grant
            self._append_audit(
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
                )
            )
            return grant

    async def redeem_support_grant(
        self,
        *,
        target_tenant_id: str,
        grant_id: UUID,
        operator: ActorRef,
    ) -> TenantAccessContext:
        now = datetime.now(UTC)
        async with self._lock:
            profile = self._profiles.get(target_tenant_id)
            grant = self._grants.get((target_tenant_id, grant_id))
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
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_USED,
                    actor=context.audited_actor(),
                    occurred_at=now,
                    elevation_id=grant.grant_id,
                    reason=grant.reason,
                    ticket_reference=grant.ticket_reference,
                )
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
        now = datetime.now(UTC)
        async with self._lock:
            grant = self._grants.get((target_tenant_id, grant_id))
            if grant is None:
                raise KeyError(grant_id)
            if not self._same_actor_identity(grant.operator, actor):
                self._require_support_authority(actor)
            if grant.revoked_at is not None:
                return
            revoked = grant.model_copy(update={"revoked_at": now})
            self._grants[(target_tenant_id, grant_id)] = revoked
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_REVOKED,
                    actor=actor,
                    occurred_at=now,
                    elevation_id=grant_id,
                    reason=reason,
                    ticket_reference=grant.ticket_reference,
                )
            )

    async def set_legal_hold(
        self,
        *,
        context: TenantAccessContext,
        enabled: bool,
        reason: str,
    ) -> TenantLifecycleRecord:
        now = datetime.now(UTC)
        async with self._lock:
            self._authorize_locked(context, TenantCapability.LEGAL_HOLD, at=now)
            tenant_id = context.tenant_id
            current = self._lifecycle.get(tenant_id)
            status = current.status if current is not None else TenantLifecycleStatus.ACTIVE
            if enabled and status is TenantLifecycleStatus.DELETION_SCHEDULED:
                status = TenantLifecycleStatus.ACTIVE
            audited_actor = context.audited_actor()
            record = TenantLifecycleRecord(
                tenant_id=tenant_id,
                status=status,
                legal_hold=enabled,
                updated_at=now,
                updated_by=audited_actor,
                elevation_id=context.elevation_id,
            )
            self._lifecycle[tenant_id] = record
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=tenant_id,
                    event_type=(
                        TenantAuditEventType.LEGAL_HOLD_PLACED
                        if enabled
                        else TenantAuditEventType.LEGAL_HOLD_RELEASED
                    ),
                    actor=audited_actor,
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=reason,
                    ticket_reference=context.ticket_reference,
                )
            )
            return record

    async def schedule_deletion(
        self,
        *,
        context: TenantAccessContext,
        not_before: datetime,
        reason: str,
    ) -> TenantLifecycleRecord:
        now = datetime.now(UTC)
        if not_before.tzinfo is None or not_before <= now:
            raise ValueError("tenant deletion must be scheduled for a future aware datetime")
        async with self._lock:
            self._authorize_locked(context, TenantCapability.DELETE, at=now)
            tenant_id = context.tenant_id
            current = self._lifecycle.get(tenant_id)
            if current is not None and current.legal_hold:
                raise AuthorizationDeniedError("tenant deletion is blocked by legal hold")
            audited_actor = context.audited_actor()
            record = TenantLifecycleRecord(
                tenant_id=tenant_id,
                status=TenantLifecycleStatus.DELETION_SCHEDULED,
                legal_hold=False,
                deletion_not_before=not_before,
                updated_at=now,
                updated_by=audited_actor,
                elevation_id=context.elevation_id,
            )
            self._lifecycle[tenant_id] = record
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=tenant_id,
                    event_type=TenantAuditEventType.DELETION_SCHEDULED,
                    actor=audited_actor,
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=reason,
                    ticket_reference=context.ticket_reference,
                    attributes={"not_before": not_before.isoformat()},
                )
            )
            return record

    async def complete_deletion(
        self,
        *,
        context: TenantAccessContext,
    ) -> TenantLifecycleRecord:
        now = datetime.now(UTC)
        async with self._lock:
            self._authorize_locked(context, TenantCapability.DELETE, at=now)
            tenant_id = context.tenant_id
            current = self._lifecycle.get(tenant_id)
            if (
                current is None
                or current.status is not TenantLifecycleStatus.DELETION_SCHEDULED
                or current.deletion_not_before is None
                or now < current.deletion_not_before
                or current.legal_hold
            ):
                raise AuthorizationDeniedError("tenant deletion is not currently permitted")
            audited_actor = context.audited_actor()
            deleted = current.model_copy(
                update={
                    "status": TenantLifecycleStatus.DELETED,
                    "deletion_not_before": None,
                    "updated_at": now,
                    "updated_by": audited_actor,
                    "elevation_id": context.elevation_id,
                }
            )
            self._lifecycle[tenant_id] = deleted
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=tenant_id,
                    event_type=TenantAuditEventType.DELETION_COMPLETED,
                    actor=audited_actor,
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=context.reason,
                    ticket_reference=context.ticket_reference,
                )
            )
            return deleted

    async def export_manifest(
        self,
        *,
        context: TenantAccessContext,
        record_counts: dict[str, int],
        content_digests: tuple[str, ...] = (),
    ) -> TenantExportManifest:
        if any(value < 0 for value in record_counts.values()):
            raise ValueError("tenant export record counts cannot be negative")
        now = datetime.now(UTC)
        async with self._lock:
            self._authorize_locked(context, TenantCapability.EXPORT, at=now)
            audited_actor = context.audited_actor()
            manifest = TenantExportManifest(
                export_id=uuid4(),
                tenant_id=context.tenant_id,
                created_at=now,
                created_by=audited_actor,
                elevation_id=context.elevation_id,
                record_counts=record_counts,
                content_digests=content_digests,
                legal_hold_preserved=True,
            )
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=context.tenant_id,
                    event_type=TenantAuditEventType.EXPORT_CREATED,
                    actor=audited_actor,
                    occurred_at=now,
                    elevation_id=context.elevation_id,
                    reason=context.reason,
                    ticket_reference=context.ticket_reference,
                    attributes={"manifest_digest": sha256_hex(manifest)},
                )
            )
            return manifest

    async def list_audit(
        self,
        *,
        context: TenantAccessContext,
    ) -> tuple[TenantAuditEvent, ...]:
        async with self._lock:
            self._authorize_locked(context, TenantCapability.READ)
            return tuple(self._audit.get(context.tenant_id, ()))

    def _authorize_locked(
        self,
        context: TenantAccessContext,
        capability: TenantCapability,
        *,
        at: datetime | None = None,
    ) -> None:
        now = at or datetime.now(UTC)
        try:
            context.require(capability, at=now)
        except PermissionError as exc:
            raise AuthorizationDeniedError(str(exc)) from exc
        if context.mode is TenantAccessMode.TENANT:
            return
        if context.elevation_id is None:
            raise AuthorizationDeniedError("support access has no elevation identity")
        profile = self._profiles.get(context.tenant_id)
        grant = self._grants.get((context.tenant_id, context.elevation_id))
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

    def _append_audit(self, event: TenantAuditEvent) -> None:
        self._audit.setdefault(event.tenant_id, []).append(event)

    @staticmethod
    def _same_actor_identity(left: ActorRef, right: ActorRef) -> bool:
        return (
            left.kind == right.kind
            and left.id == right.id
            and left.tenant_id == right.tenant_id
            and left.workload_identity == right.workload_identity
        )

    @staticmethod
    def _require_support_authority(actor: ActorRef) -> None:
        if actor.attributes.get("prodkit.support_authority") != "true":
            raise AuthorizationDeniedError("support elevation issuer lacks support authority")

    @staticmethod
    def _require_support_operator(actor: ActorRef) -> None:
        if actor.attributes.get("prodkit.support_operator") != "true":
            raise AuthorizationDeniedError("support elevation recipient is not a support operator")


class TenantCacheNamespace:
    """Deterministic tenant cache key namespace; payload keys cannot escape their tenant."""

    @staticmethod
    def key(*, tenant_id: str, namespace: str, key: str) -> str:
        if not tenant_id.strip() or not namespace.strip() or not key.strip():
            raise ValueError("tenant cache key components must be non-blank")
        tenant_partition = sha256_hex({"tenant_id": tenant_id})
        return f"prodkit:{tenant_partition}:{namespace}:{key}"
