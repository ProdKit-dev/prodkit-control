from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractModel, NonBlankStr
from .identity import ActorRef


class TenantAccessMode(StrEnum):
    TENANT = "tenant"
    SUPPORT = "support"


class TenantCapability(StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    APPROVE = "approve"
    EXPORT = "export"
    DELETE = "delete"
    LEGAL_HOLD = "legal_hold"
    CONFIGURE = "configure"


class TenantAccessContext(ContractModel):
    """Authoritative tenant context carried across service/adapter boundaries."""

    schema_name: str = "prodkit.tenant-access-context"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    actor: ActorRef
    mode: TenantAccessMode = TenantAccessMode.TENANT
    capabilities: tuple[TenantCapability, ...] = ()
    elevation_id: UUID | None = None
    reason: NonBlankStr | None = None
    ticket_reference: NonBlankStr | None = None
    issued_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> TenantAccessContext:
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("tenant access context must expire after issuance")
        if self.mode is TenantAccessMode.TENANT:
            if self.actor.tenant_id != self.tenant_id:
                raise ValueError("ordinary tenant access cannot cross tenant boundaries")
            if self.elevation_id is not None:
                raise ValueError("ordinary tenant access cannot carry a support elevation")
        else:
            if self.elevation_id is None or not self.reason or not self.ticket_reference:
                raise ValueError("support access requires elevation, reason, and ticket reference")
            if not self.capabilities:
                raise ValueError("support access requires explicit capabilities")
        return self

    def require(self, capability: TenantCapability, *, at: datetime | None = None) -> None:
        now = at or datetime.now(UTC)
        if self.expires_at is not None and now >= self.expires_at:
            raise PermissionError("tenant access context has expired")
        if capability not in self.capabilities:
            raise PermissionError(f"tenant capability {capability.value!r} is not granted")


class SupportElevationGrant(ContractModel):
    """Time-bounded, capability-bounded support access to one target tenant."""

    schema_name: str = "prodkit.support-elevation-grant"
    schema_version: str = "1.0.0"
    grant_id: UUID
    target_tenant_id: NonBlankStr
    operator: ActorRef
    capabilities: tuple[TenantCapability, ...]
    reason: NonBlankStr
    ticket_reference: NonBlankStr
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_grant(self) -> SupportElevationGrant:
        if not self.capabilities:
            raise ValueError("support elevation requires at least one capability")
        if self.expires_at <= self.issued_at:
            raise ValueError("support elevation must expire after issuance")
        if self.revoked_at is not None and self.revoked_at < self.issued_at:
            raise ValueError("support elevation cannot be revoked before issuance")
        return self

    def active(self, *, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        return self.revoked_at is None and self.issued_at <= now < self.expires_at


class TenantIsolationProfile(ContractModel):
    """Tenant-local policy/configuration selectors without provider coupling."""

    schema_name: str = "prodkit.tenant-isolation-profile"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    policy_profile: NonBlankStr = "default"
    signing_profile: NonBlankStr = "default"
    retention_profile: NonBlankStr = "default"
    executor_profile: NonBlankStr = "default"
    storage_partition: NonBlankStr
    cache_namespace: NonBlankStr
    allow_support_access: bool = False
    attributes: dict[str, str] = Field(default_factory=dict)


class TenantLifecycleStatus(StrEnum):
    ACTIVE = "active"
    DELETION_SCHEDULED = "deletion_scheduled"
    DELETED = "deleted"


class TenantLifecycleRecord(ContractModel):
    schema_name: str = "prodkit.tenant-lifecycle"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    status: TenantLifecycleStatus = TenantLifecycleStatus.ACTIVE
    legal_hold: bool = False
    deletion_not_before: AwareDatetime | None = None
    updated_at: AwareDatetime
    updated_by: ActorRef

    @model_validator(mode="after")
    def validate_lifecycle(self) -> TenantLifecycleRecord:
        if self.updated_by.tenant_id != self.tenant_id and not self.updated_by.attributes.get(
            "prodkit.support_elevation"
        ):
            raise ValueError("tenant lifecycle mutations require tenant or elevated support identity")
        if self.legal_hold and self.status is TenantLifecycleStatus.DELETED:
            raise ValueError("a tenant under legal hold cannot be marked deleted")
        if self.status is TenantLifecycleStatus.DELETION_SCHEDULED:
            if self.deletion_not_before is None:
                raise ValueError("scheduled deletion requires deletion_not_before")
            if self.legal_hold:
                raise ValueError("deletion cannot be scheduled while legal hold is active")
        elif self.deletion_not_before is not None:
            raise ValueError("deletion_not_before is only valid for scheduled deletion")
        return self


class TenantAuditEventType(StrEnum):
    SUPPORT_ELEVATION_ISSUED = "support_elevation_issued"
    SUPPORT_ELEVATION_USED = "support_elevation_used"
    SUPPORT_ELEVATION_REVOKED = "support_elevation_revoked"
    EXPORT_CREATED = "export_created"
    DELETION_SCHEDULED = "deletion_scheduled"
    DELETION_COMPLETED = "deletion_completed"
    LEGAL_HOLD_PLACED = "legal_hold_placed"
    LEGAL_HOLD_RELEASED = "legal_hold_released"
    CONFIGURATION_CHANGED = "configuration_changed"


class TenantAuditEvent(ContractModel):
    schema_name: str = "prodkit.tenant-audit-event"
    schema_version: str = "1.0.0"
    audit_id: UUID
    tenant_id: NonBlankStr
    event_type: TenantAuditEventType
    actor: ActorRef
    occurred_at: AwareDatetime
    elevation_id: UUID | None = None
    reason: NonBlankStr | None = None
    ticket_reference: NonBlankStr | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class TenantExportManifest(ContractModel):
    schema_name: str = "prodkit.tenant-export-manifest"
    schema_version: str = "1.0.0"
    export_id: UUID
    tenant_id: NonBlankStr
    created_at: AwareDatetime
    created_by: ActorRef
    record_counts: dict[str, int] = Field(default_factory=dict)
    content_digests: tuple[NonBlankStr, ...] = ()
    legal_hold_preserved: bool = True
