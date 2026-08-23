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
    """Standalone tenant configuration, elevation, lifecycle, and audit store.

    Production adapters can persist the same canonical records in a database or governance service.
    All lookups require the target tenant; there is no unscoped tenant-data iterator.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, TenantIsolationProfile] = {}
        self._grants: dict[tuple[str, UUID], SupportElevationGrant] = {}
        self._lifecycle: dict[str, TenantLifecycleRecord] = {}
        self._audit: dict[str, list[TenantAuditEvent]] = {}
        self._lock = asyncio.Lock()

    async def put_profile(self, profile: TenantIsolationProfile, *, actor: ActorRef) -> None:
        if actor.tenant_id != profile.tenant_id:
            raise AuthorizationDeniedError("tenant profile mutation crossed tenant boundary")
        async with self._lock:
            self._profiles[profile.tenant_id] = profile
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=profile.tenant_id,
                    event_type=TenantAuditEventType.CONFIGURATION_CHANGED,
                    actor=actor,
                    occurred_at=datetime.now(UTC),
                    attributes={"profile_digest": sha256_hex(profile)},
                )
            )

    async def get_profile(self, tenant_id: str) -> TenantIsolationProfile | None:
        async with self._lock:
            return self._profiles.get(tenant_id)

    async def issue_support_grant(
        self,
        *,
        target_tenant_id: str,
        operator: ActorRef,
        capabilities: tuple[TenantCapability, ...],
        reason: str,
        ticket_reference: str,
        ttl_seconds: int = 900,
    ) -> SupportElevationGrant:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise ValueError("support elevation TTL must be between 30 and 3600 seconds")
        profile = self._profiles.get(target_tenant_id)
        if profile is None or not profile.allow_support_access:
            raise AuthorizationDeniedError("target tenant has not enabled support elevation")
        now = datetime.now(UTC)
        grant = SupportElevationGrant(
            grant_id=uuid4(),
            target_tenant_id=target_tenant_id,
            operator=operator,
            capabilities=capabilities,
            reason=reason,
            ticket_reference=ticket_reference,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        async with self._lock:
            self._grants[(target_tenant_id, grant.grant_id)] = grant
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_ISSUED,
                    actor=operator,
                    occurred_at=now,
                    elevation_id=grant.grant_id,
                    reason=reason,
                    ticket_reference=ticket_reference,
                    attributes={"capabilities": ",".join(cap.value for cap in capabilities)},
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
        async with self._lock:
            grant = self._grants.get((target_tenant_id, grant_id))
            if grant is None or grant.operator != operator or not grant.active():
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
                    actor=operator,
                    occurred_at=datetime.now(UTC),
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
        async with self._lock:
            grant = self._grants.get((target_tenant_id, grant_id))
            if grant is None:
                raise KeyError(grant_id)
            revoked = grant.model_copy(update={"revoked_at": datetime.now(UTC)})
            self._grants[(target_tenant_id, grant_id)] = revoked
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=target_tenant_id,
                    event_type=TenantAuditEventType.SUPPORT_ELEVATION_REVOKED,
                    actor=actor,
                    occurred_at=datetime.now(UTC),
                    elevation_id=grant_id,
                    reason=reason,
                    ticket_reference=grant.ticket_reference,
                )
            )

    async def set_legal_hold(
        self, *, tenant_id: str, actor: ActorRef, enabled: bool, reason: str
    ) -> TenantLifecycleRecord:
        self._require_tenant_or_elevated(actor, tenant_id)
        now = datetime.now(UTC)
        async with self._lock:
            current = self._lifecycle.get(tenant_id)
            status = current.status if current is not None else TenantLifecycleStatus.ACTIVE
            if enabled and status is TenantLifecycleStatus.DELETION_SCHEDULED:
                status = TenantLifecycleStatus.ACTIVE
            record = TenantLifecycleRecord(
                tenant_id=tenant_id,
                status=status,
                legal_hold=enabled,
                updated_at=now,
                updated_by=actor,
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
                    actor=actor,
                    occurred_at=now,
                    reason=reason,
                )
            )
            return record

    async def schedule_deletion(
        self,
        *,
        tenant_id: str,
        actor: ActorRef,
        not_before: datetime,
        reason: str,
    ) -> TenantLifecycleRecord:
        self._require_tenant_or_elevated(actor, tenant_id)
        now = datetime.now(UTC)
        if not_before.tzinfo is None or not_before <= now:
            raise ValueError("tenant deletion must be scheduled for a future aware datetime")
        async with self._lock:
            current = self._lifecycle.get(tenant_id)
            if current is not None and current.legal_hold:
                raise AuthorizationDeniedError("tenant deletion is blocked by legal hold")
            record = TenantLifecycleRecord(
                tenant_id=tenant_id,
                status=TenantLifecycleStatus.DELETION_SCHEDULED,
                legal_hold=False,
                deletion_not_before=not_before,
                updated_at=now,
                updated_by=actor,
            )
            self._lifecycle[tenant_id] = record
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=tenant_id,
                    event_type=TenantAuditEventType.DELETION_SCHEDULED,
                    actor=actor,
                    occurred_at=now,
                    reason=reason,
                    attributes={"not_before": not_before.isoformat()},
                )
            )
            return record

    async def complete_deletion(self, *, tenant_id: str, actor: ActorRef) -> TenantLifecycleRecord:
        self._require_tenant_or_elevated(actor, tenant_id)
        now = datetime.now(UTC)
        async with self._lock:
            current = self._lifecycle.get(tenant_id)
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
                    "updated_by": actor,
                }
            )
            self._lifecycle[tenant_id] = deleted
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=tenant_id,
                    event_type=TenantAuditEventType.DELETION_COMPLETED,
                    actor=actor,
                    occurred_at=now,
                )
            )
            return deleted

    async def export_manifest(
        self,
        *,
        tenant_id: str,
        actor: ActorRef,
        record_counts: dict[str, int],
        content_digests: tuple[str, ...] = (),
    ) -> TenantExportManifest:
        self._require_tenant_or_elevated(actor, tenant_id)
        if any(value < 0 for value in record_counts.values()):
            raise ValueError("tenant export record counts cannot be negative")
        now = datetime.now(UTC)
        manifest = TenantExportManifest(
            export_id=uuid4(),
            tenant_id=tenant_id,
            created_at=now,
            created_by=actor,
            record_counts=record_counts,
            content_digests=content_digests,
            legal_hold_preserved=True,
        )
        async with self._lock:
            self._append_audit(
                TenantAuditEvent(
                    audit_id=uuid4(),
                    tenant_id=tenant_id,
                    event_type=TenantAuditEventType.EXPORT_CREATED,
                    actor=actor,
                    occurred_at=now,
                    attributes={"manifest_digest": sha256_hex(manifest)},
                )
            )
        return manifest

    async def list_audit(self, tenant_id: str) -> tuple[TenantAuditEvent, ...]:
        async with self._lock:
            return tuple(self._audit.get(tenant_id, ()))

    def _append_audit(self, event: TenantAuditEvent) -> None:
        self._audit.setdefault(event.tenant_id, []).append(event)

    @staticmethod
    def _require_tenant_or_elevated(actor: ActorRef, tenant_id: str) -> None:
        if actor.tenant_id == tenant_id:
            return
        if actor.attributes.get("prodkit.support_elevation"):
            return
        raise AuthorizationDeniedError("tenant lifecycle operation crossed tenant boundary")


class TenantCacheNamespace:
    """Deterministic tenant cache key namespace; payload keys cannot escape their tenant."""

    @staticmethod
    def key(*, tenant_id: str, namespace: str, key: str) -> str:
        if not tenant_id.strip() or not namespace.strip() or not key.strip():
            raise ValueError("tenant cache key components must be non-blank")
        tenant_partition = sha256_hex({"tenant_id": tenant_id})
        return f"prodkit:{tenant_partition}:{namespace}:{key}"
