from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .attestations import TrustRootPolicy
from .base import ContractModel, NonBlankStr, Sha256
from .identity import ActorRef


class GovernanceRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GovernanceTargetType(StrEnum):
    RETENTION_POLICY = "retention_policy"
    TRUST_ROOT_POLICY = "trust_root_policy"
    LEGAL_HOLD_RELEASE = "legal_hold_release"
    TENANT_CONFIGURATION = "tenant_configuration"
    COMPATIBILITY_POLICY = "compatibility_policy"
    DEPRECATION_POLICY = "deprecation_policy"


class GovernanceChangeStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    CANCELLED = "cancelled"


class GovernanceApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RetentionDisposition(StrEnum):
    RETAIN = "retain"
    DELETE = "delete"


class LegalHoldStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"


class GovernanceAuditEventType(StrEnum):
    CHANGE_PROPOSED = "change_proposed"
    CHANGE_APPROVED = "change_approved"
    CHANGE_REJECTED = "change_rejected"
    CHANGE_APPLIED = "change_applied"
    RETENTION_EVALUATED = "retention_evaluated"
    RETENTION_DELETION_INTENT_RECORDED = "retention_deletion_intent_recorded"
    RETENTION_DELETION_CANCELLED = "retention_deletion_cancelled"
    RETENTION_DELETION_EXECUTED = "retention_deletion_executed"
    LEGAL_HOLD_PLACED = "legal_hold_placed"
    LEGAL_HOLD_RELEASED = "legal_hold_released"
    TRUST_ROOT_ACTIVATED = "trust_root_activated"
    EVIDENCE_EXPORT_CREATED = "evidence_export_created"
    EVIDENCE_IMPORT_VERIFIED = "evidence_import_verified"
    MIGRATION_RECORDED = "migration_recorded"


class RetentionRule(ContractModel):
    resource_type: NonBlankStr
    retain_for_seconds: int | None = Field(default=None, ge=0)
    deletion_grace_seconds: int = Field(default=0, ge=0)
    deletion_allowed: bool = True

    @model_validator(mode="after")
    def validate_rule(self) -> RetentionRule:
        if not self.deletion_allowed and self.retain_for_seconds is not None:
            raise ValueError("non-deletable retention rules must retain indefinitely")
        return self


class RetentionPolicy(ContractModel):
    schema_name: str = "prodkit.retention-policy"
    schema_version: str = "1.0.0"
    policy_id: UUID
    tenant_id: NonBlankStr
    revision: int = Field(ge=1)
    effective_at: AwareDatetime
    default_retain_for_seconds: int | None = Field(default=None, ge=0)
    rules: tuple[RetentionRule, ...]
    created_at: AwareDatetime
    created_by: ActorRef

    @model_validator(mode="after")
    def validate_policy(self) -> RetentionPolicy:
        resource_types = tuple(rule.resource_type for rule in self.rules)
        if len(resource_types) != len(set(resource_types)):
            raise ValueError("retention policy resource types must be unique")
        if self.created_at > self.effective_at:
            raise ValueError("retention policy cannot become effective before it is created")
        return self

    def rule_for(self, resource_type: str) -> RetentionRule | None:
        return next((rule for rule in self.rules if rule.resource_type == resource_type), None)


class RetentionCandidate(ContractModel):
    schema_name: str = "prodkit.retention-candidate"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    resource_type: NonBlankStr
    resource_id: NonBlankStr
    created_at: AwareDatetime
    content_sha256: Sha256 | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class RetentionDecision(ContractModel):
    schema_name: str = "prodkit.retention-decision"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    resource_type: NonBlankStr
    resource_id: NonBlankStr
    disposition: RetentionDisposition
    evaluated_at: AwareDatetime
    policy_id: UUID
    policy_revision: int = Field(ge=1)
    delete_not_before: AwareDatetime | None = None
    legal_hold_ids: tuple[UUID, ...] = ()
    reason: NonBlankStr

    @model_validator(mode="after")
    def validate_decision(self) -> RetentionDecision:
        if self.disposition is RetentionDisposition.DELETE:
            if self.delete_not_before is None:
                raise ValueError("delete decisions require delete_not_before")
            if self.legal_hold_ids:
                raise ValueError("legal hold cannot coexist with a delete decision")
        elif self.delete_not_before is not None:
            raise ValueError("retain decisions cannot carry delete_not_before")
        return self


class RetentionExecutionRecord(ContractModel):
    schema_name: str = "prodkit.retention-execution"
    schema_version: str = "1.0.0"
    execution_id: UUID
    tenant_id: NonBlankStr
    resource_type: NonBlankStr
    resource_id: NonBlankStr
    executed_at: AwareDatetime
    executed_by: ActorRef
    policy_id: UUID
    policy_revision: int = Field(ge=1)
    content_sha256: Sha256 | None = None
    deletion_reference: NonBlankStr


class LegalHold(ContractModel):
    schema_name: str = "prodkit.legal-hold"
    schema_version: str = "1.0.0"
    hold_id: UUID
    tenant_id: NonBlankStr
    status: LegalHoldStatus = LegalHoldStatus.ACTIVE
    reason: NonBlankStr
    case_reference: NonBlankStr
    resource_types: tuple[NonBlankStr, ...] = ()
    resource_ids: tuple[NonBlankStr, ...] = ()
    placed_at: AwareDatetime
    placed_by: ActorRef
    released_at: AwareDatetime | None = None
    released_by: ActorRef | None = None
    release_change_request_id: UUID | None = None

    @model_validator(mode="after")
    def validate_hold(self) -> LegalHold:
        if len(self.resource_types) != len(set(self.resource_types)):
            raise ValueError("legal-hold resource types must be unique")
        if len(self.resource_ids) != len(set(self.resource_ids)):
            raise ValueError("legal-hold resource ids must be unique")
        if self.status is LegalHoldStatus.ACTIVE:
            if (
                self.released_at is not None
                or self.released_by is not None
                or self.release_change_request_id is not None
            ):
                raise ValueError("active legal holds cannot carry release metadata")
        else:
            if (
                self.released_at is None
                or self.released_by is None
                or self.release_change_request_id is None
            ):
                raise ValueError("released legal holds require complete release metadata")
            if self.released_at < self.placed_at:
                raise ValueError("legal hold cannot be released before placement")
        return self

    def applies_to(self, candidate: RetentionCandidate) -> bool:
        if self.status is not LegalHoldStatus.ACTIVE or self.tenant_id != candidate.tenant_id:
            return False
        if self.resource_types and candidate.resource_type not in self.resource_types:
            return False
        if self.resource_ids and candidate.resource_id not in self.resource_ids:
            return False
        return True


class GovernanceChangeRequest(ContractModel):
    schema_name: str = "prodkit.governance-change-request"
    schema_version: str = "1.0.0"
    request_id: UUID
    tenant_id: NonBlankStr
    target_type: GovernanceTargetType
    target_id: NonBlankStr
    proposed_digest: Sha256
    expected_current_digest: Sha256 | None = None
    risk: GovernanceRisk
    reason: NonBlankStr
    ticket_reference: NonBlankStr
    proposed_at: AwareDatetime
    proposed_by: ActorRef
    status: GovernanceChangeStatus = GovernanceChangeStatus.PROPOSED
    approved_at: AwareDatetime | None = None
    approved_by: ActorRef | None = None
    applied_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_change(self) -> GovernanceChangeRequest:
        approved = self.status in {
            GovernanceChangeStatus.APPROVED,
            GovernanceChangeStatus.APPLIED,
        }
        if approved:
            if self.approved_at is None or self.approved_by is None:
                raise ValueError("approved governance changes require approval evidence")
            if self.risk in {GovernanceRisk.HIGH, GovernanceRisk.CRITICAL}:
                if (
                    self.approved_by.kind == self.proposed_by.kind
                    and self.approved_by.id == self.proposed_by.id
                    and self.approved_by.tenant_id == self.proposed_by.tenant_id
                ):
                    raise ValueError("high-risk governance changes require independent approval")
        elif self.approved_at is not None or self.approved_by is not None:
            raise ValueError("unapproved governance changes cannot carry approval evidence")
        if self.status is GovernanceChangeStatus.APPLIED and self.applied_at is None:
            raise ValueError("applied governance changes require applied_at")
        if self.status is not GovernanceChangeStatus.APPLIED and self.applied_at is not None:
            raise ValueError("only applied governance changes can carry applied_at")
        return self


class GovernanceApproval(ContractModel):
    schema_name: str = "prodkit.governance-approval"
    schema_version: str = "1.0.0"
    approval_id: UUID
    request_id: UUID
    tenant_id: NonBlankStr
    decision: GovernanceApprovalDecision
    actor: ActorRef
    occurred_at: AwareDatetime
    reason: NonBlankStr


class GovernedTrustRoot(ContractModel):
    schema_name: str = "prodkit.governed-trust-root"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    revision: int = Field(ge=1)
    policy: TrustRootPolicy
    policy_sha256: Sha256
    activated_at: AwareDatetime
    retired_at: AwareDatetime | None = None
    change_request_id: UUID

    @model_validator(mode="after")
    def validate_root(self) -> GovernedTrustRoot:
        if self.retired_at is not None and self.retired_at <= self.activated_at:
            raise ValueError("trust-root retirement must follow activation")
        return self


class KeyRotationPlan(ContractModel):
    schema_name: str = "prodkit.key-rotation-plan"
    schema_version: str = "1.0.0"
    rotation_id: UUID
    tenant_id: NonBlankStr
    from_revision: int = Field(ge=1)
    to_revision: int = Field(ge=2)
    activate_at: AwareDatetime
    overlap_until: AwareDatetime
    change_request_id: UUID
    emergency: bool = False

    @model_validator(mode="after")
    def validate_rotation(self) -> KeyRotationPlan:
        if self.to_revision <= self.from_revision:
            raise ValueError("key rotation must advance trust-root revision")
        if self.overlap_until < self.activate_at:
            raise ValueError("key rotation overlap cannot end before activation")
        return self


class TrustRootHistory(ContractModel):
    schema_name: str = "prodkit.trust-root-history"
    schema_version: str = "1.0.0"
    tenant_id: NonBlankStr
    roots: tuple[GovernedTrustRoot, ...]

    @model_validator(mode="after")
    def validate_history(self) -> TrustRootHistory:
        if not self.roots:
            raise ValueError("trust-root history requires at least one revision")
        revisions = tuple(root.revision for root in self.roots)
        if revisions != tuple(sorted(revisions)) or len(revisions) != len(set(revisions)):
            raise ValueError("trust-root revisions must be unique and ascending")
        if sum(root.retired_at is None for root in self.roots) != 1:
            raise ValueError("trust-root history requires exactly one current revision")
        return self

    def policy_for(self, signed_at: datetime, *, key_id: str | None = None) -> TrustRootPolicy:
        matches = tuple(
            root
            for root in self.roots
            if root.activated_at <= signed_at
            and (root.retired_at is None or signed_at < root.retired_at)
            and (key_id is None or any(key.key_id == key_id for key in root.policy.trusted_keys))
        )
        if len(matches) != 1:
            raise ValueError("no unique trust-root revision covers the signing evidence")
        return matches[0].policy


class EvidenceTransferManifest(ContractModel):
    schema_name: str = "prodkit.evidence-transfer-manifest"
    schema_version: str = "1.0.0"
    transfer_id: UUID
    tenant_id: NonBlankStr
    created_at: AwareDatetime
    created_by: ActorRef
    source_control_version: NonBlankStr
    source_schema_version: int = Field(ge=1)
    archive_sha256: Sha256
    bundle_manifest_sha256: Sha256
    trust_root_revision: int | None = Field(default=None, ge=1)
    legal_hold_preserved: bool = True


class EvidenceImportReceipt(ContractModel):
    schema_name: str = "prodkit.evidence-import-receipt"
    schema_version: str = "1.0.0"
    import_id: UUID
    transfer_id: UUID
    tenant_id: NonBlankStr
    imported_at: AwareDatetime
    imported_by: ActorRef
    source_control_version: NonBlankStr
    source_schema_version: int = Field(ge=1)
    archive_sha256: Sha256
    verification_id: UUID
    verification_sha256: Sha256
    trust_anchor_sha256: Sha256
    verified: bool = True

    @model_validator(mode="after")
    def validate_import(self) -> EvidenceImportReceipt:
        if not self.verified:
            raise ValueError("evidence import receipts are emitted only after verification")
        return self


class MigrationPath(ContractModel):
    from_schema_version: int = Field(ge=1)
    to_schema_version: int = Field(ge=2)
    minimum_control_version: NonBlankStr
    requires_backup: bool = True
    reversible: bool = False

    @model_validator(mode="after")
    def validate_path(self) -> MigrationPath:
        if self.to_schema_version != self.from_schema_version + 1:
            raise ValueError("supported migration paths must be single-step and sequential")
        return self


class DeprecationWindow(ContractModel):
    surface: NonBlankStr
    deprecated_in_version: NonBlankStr
    removal_not_before_version: NonBlankStr
    announced_at: AwareDatetime
    replacement: NonBlankStr | None = None


class CompatibilityPolicy(ContractModel):
    schema_name: str = "prodkit.compatibility-policy"
    schema_version: str = "1.0.0"
    current_schema_version: int = Field(ge=1)
    minimum_supported_schema_version: int = Field(ge=1)
    migration_paths: tuple[MigrationPath, ...]
    deprecations: tuple[DeprecationWindow, ...] = ()

    @model_validator(mode="after")
    def validate_compatibility(self) -> CompatibilityPolicy:
        if self.minimum_supported_schema_version > self.current_schema_version:
            raise ValueError("minimum supported schema version cannot exceed current")
        expected = tuple(range(self.minimum_supported_schema_version, self.current_schema_version))
        actual = tuple(path.from_schema_version for path in self.migration_paths)
        if actual != expected:
            raise ValueError("compatibility policy must enumerate every supported upgrade step")
        if any(
            path.to_schema_version != path.from_schema_version + 1 for path in self.migration_paths
        ):
            raise ValueError("compatibility policy contains a non-sequential migration")
        return self

    def path_from(self, schema_version: int) -> tuple[MigrationPath, ...]:
        if schema_version < self.minimum_supported_schema_version:
            raise ValueError("database schema is older than the supported upgrade window")
        if schema_version > self.current_schema_version:
            raise ValueError("database schema is newer than this runtime")
        return tuple(
            path for path in self.migration_paths if path.from_schema_version >= schema_version
        )


class GovernanceAuditEvent(ContractModel):
    schema_name: str = "prodkit.governance-audit-event"
    schema_version: str = "1.0.0"
    event_id: UUID
    tenant_id: NonBlankStr
    event_type: GovernanceAuditEventType
    actor: ActorRef
    occurred_at: AwareDatetime
    request_id: UUID | None = None
    target_type: GovernanceTargetType | None = None
    target_id: NonBlankStr | None = None
    before_digest: Sha256 | None = None
    after_digest: Sha256 | None = None
    reason: NonBlankStr
    ticket_reference: NonBlankStr | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
