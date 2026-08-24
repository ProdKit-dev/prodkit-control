from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractModel, NonBlankStr, Sha256
from .execution import ExecutionAttemptState
from .identity import ActorRef


class RecoveryComponent(StrEnum):
    LEDGER = "ledger"
    LINEAGE = "lineage"
    CONFIGURATION = "configuration"
    ARTIFACT_METADATA = "artifact_metadata"
    OBJECT_STORE = "object_store"
    IDEMPOTENCY = "idempotency"
    EXECUTION_ATTEMPTS = "execution_attempts"
    GOVERNANCE = "governance"


class RecoveryIntegrityStatus(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"


class RestoreStatus(StrEnum):
    VERIFIED = "verified"
    DEGRADED = "degraded"
    FAILED = "failed"


class RecoveryFindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class UncertainRecoveryDisposition(StrEnum):
    RECONCILE_REQUIRED = "reconcile_required"
    MATCHED_SUCCESS = "matched_success"
    MATCHED_FAILURE = "matched_failure"
    NOT_OBSERVED = "not_observed"
    UNVERIFIABLE = "unverifiable"


class BreakGlassCapability(StrEnum):
    RESTORE = "restore"
    FAILOVER = "failover"
    INTEGRITY_SCAN = "integrity_scan"
    RECONCILE = "reconcile"
    CONFIGURE_RECOVERY = "configure_recovery"


class RecoveryAuditEventType(StrEnum):
    PROFILE_PUBLISHED = "profile_published"
    BACKUP_RECORDED = "backup_recorded"
    RESTORE_PLANNED = "restore_planned"
    BREAK_GLASS_ISSUED = "break_glass_issued"
    BREAK_GLASS_USED = "break_glass_used"
    BREAK_GLASS_REVOKED = "break_glass_revoked"
    INTEGRITY_SCAN_RECORDED = "integrity_scan_recorded"
    UNCERTAIN_ATTEMPT_RECONCILED = "uncertain_attempt_reconciled"
    RECOVERY_GAP_RECONCILED = "recovery_gap_reconciled"
    RESTORE_COMPLETED = "restore_completed"
    GAME_DAY_RECORDED = "game_day_recorded"


class ReliabilityProfile(ContractModel):
    schema_name: str = "prodkit.reliability-profile"
    schema_version: str = "1.0.0"
    profile_id: NonBlankStr
    tenant_id: NonBlankStr
    revision: int = Field(ge=1)
    rpo_seconds: int = Field(ge=0)
    rto_seconds: int = Field(gt=0)
    backup_interval_seconds: int = Field(gt=0)
    max_backup_age_seconds: int = Field(gt=0)
    restore_exercise_interval_seconds: int = Field(gt=0)
    required_components: tuple[RecoveryComponent, ...]
    max_break_glass_seconds: int = Field(default=900, ge=60, le=3600)
    effective_at: AwareDatetime
    created_at: AwareDatetime
    created_by: ActorRef

    @model_validator(mode="after")
    def validate_profile(self) -> ReliabilityProfile:
        if self.created_by.tenant_id != self.tenant_id:
            raise ValueError("reliability profile actor must belong to the tenant")
        if self.created_at > self.effective_at:
            raise ValueError("reliability profile cannot be effective before creation")
        if not self.required_components:
            raise ValueError("reliability profile requires at least one recovery component")
        if len(self.required_components) != len(set(self.required_components)):
            raise ValueError("recovery components must be unique")
        if self.backup_interval_seconds > self.max_backup_age_seconds:
            raise ValueError("backup interval cannot exceed maximum backup age")
        if self.rpo_seconds > 0 and self.backup_interval_seconds > self.rpo_seconds:
            raise ValueError("backup interval cannot exceed the declared RPO")
        return self


class BackupComponentRecord(ContractModel):
    component: RecoveryComponent
    reference: NonBlankStr
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    captured_at: AwareDatetime
    source_site: NonBlankStr
    encrypted: bool = True
    immutable: bool = True


class BackupManifest(ContractModel):
    schema_name: str = "prodkit.backup-manifest"
    schema_version: str = "1.0.0"
    backup_id: UUID
    tenant_id: NonBlankStr
    profile_id: NonBlankStr
    profile_revision: int = Field(ge=1)
    source_schema_version: int = Field(ge=1)
    source_control_version: NonBlankStr
    source_site: NonBlankStr
    snapshot_set_id: NonBlankStr
    recovery_point_at: AwareDatetime
    created_at: AwareDatetime
    components: tuple[BackupComponentRecord, ...]
    ledger_chain_tip_sha256: Sha256
    trusted_checkpoint_sha256: Sha256
    trust_anchor_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> BackupManifest:
        if self.recovery_point_at > self.created_at:
            raise ValueError("backup recovery point cannot be in the future")
        if not self.components:
            raise ValueError("backup manifest requires components")
        kinds = tuple(component.component for component in self.components)
        if len(kinds) != len(set(kinds)):
            raise ValueError("backup manifest requires one canonical record per component")
        if any(component.captured_at > self.created_at for component in self.components):
            raise ValueError("backup component capture cannot follow manifest creation")
        return self


class RestorePlan(ContractModel):
    schema_name: str = "prodkit.restore-plan"
    schema_version: str = "1.0.0"
    restore_id: UUID
    tenant_id: NonBlankStr
    profile_id: NonBlankStr
    profile_revision: int = Field(ge=1)
    backup_id: UUID
    target_site: NonBlankStr
    failure_detected_at: AwareDatetime
    requested_at: AwareDatetime
    requested_by: ActorRef
    break_glass_grant_id: UUID
    reconcile_uncertain: bool = True
    reconcile_recovery_gap: bool = True
    prohibit_blind_replay: bool = True

    @model_validator(mode="after")
    def validate_plan(self) -> RestorePlan:
        if self.requested_by.tenant_id != self.tenant_id:
            raise ValueError("restore requester must belong to the tenant")
        if self.requested_at < self.failure_detected_at:
            raise ValueError("restore request cannot predate failure detection")
        if (
            not self.reconcile_uncertain
            or not self.reconcile_recovery_gap
            or not self.prohibit_blind_replay
        ):
            raise ValueError(
                "restore plans must reconcile known uncertainty and the RPO gap and prohibit blind replay"
            )
        return self


class RestoredComponentObservation(ContractModel):
    component: RecoveryComponent
    reference: NonBlankStr
    sha256: Sha256
    observed_at: AwareDatetime


class RecoveryIntegrityFinding(ContractModel):
    severity: RecoveryFindingSeverity
    component: RecoveryComponent | None = None
    reference: NonBlankStr
    summary: NonBlankStr
    expected_sha256: Sha256 | None = None
    observed_sha256: Sha256 | None = None


class IntegrityScanResult(ContractModel):
    schema_name: str = "prodkit.recovery-integrity-scan"
    schema_version: str = "1.0.0"
    scan_id: UUID
    restore_id: UUID
    tenant_id: NonBlankStr
    completed_at: AwareDatetime
    status: RecoveryIntegrityStatus
    chain_verified: bool
    checkpoint_verified: bool
    trust_anchor_verified: bool
    object_store_verified: bool
    components_verified: tuple[RecoveryComponent, ...]
    findings: tuple[RecoveryIntegrityFinding, ...] = ()

    @model_validator(mode="after")
    def validate_scan(self) -> IntegrityScanResult:
        if len(self.components_verified) != len(set(self.components_verified)):
            raise ValueError("verified recovery components must be unique")
        severe = any(
            finding.severity in {RecoveryFindingSeverity.ERROR, RecoveryFindingSeverity.CRITICAL}
            for finding in self.findings
        )
        if self.status is RecoveryIntegrityStatus.VERIFIED:
            if (
                not self.chain_verified
                or not self.checkpoint_verified
                or not self.trust_anchor_verified
                or not self.object_store_verified
            ):
                raise ValueError(
                    "verified recovery requires chain, signed-checkpoint, trust-anchor, and object-store proof"
                )
            if severe:
                raise ValueError("verified recovery cannot contain error or critical findings")
        return self


class UncertainExecutionRecovery(ContractModel):
    schema_name: str = "prodkit.uncertain-execution-recovery"
    schema_version: str = "1.0.0"
    recovery_id: UUID
    restore_id: UUID
    tenant_id: NonBlankStr
    attempt_id: UUID
    action_id: UUID
    run_id: UUID
    original_state: ExecutionAttemptState = ExecutionAttemptState.UNCERTAIN
    provider_operation_id: NonBlankStr | None = None
    disposition: UncertainRecoveryDisposition
    observed_at: AwareDatetime
    evidence_reference: NonBlankStr | None = None
    replay_permitted: bool = False

    @model_validator(mode="after")
    def validate_recovery(self) -> UncertainExecutionRecovery:
        if self.original_state is not ExecutionAttemptState.UNCERTAIN:
            raise ValueError("recovery records apply only to uncertain execution attempts")
        if self.replay_permitted:
            raise ValueError(
                "disaster recovery never authorizes blind replay of an uncertain action"
            )
        if (
            self.disposition
            in {
                UncertainRecoveryDisposition.MATCHED_SUCCESS,
                UncertainRecoveryDisposition.MATCHED_FAILURE,
            }
            and self.evidence_reference is None
        ):
            raise ValueError("resolved uncertain outcomes require evidence_reference")
        return self


class RecoveryGapReconciliation(ContractModel):
    schema_name: str = "prodkit.recovery-gap-reconciliation"
    schema_version: str = "1.0.0"
    reconciliation_id: UUID
    restore_id: UUID
    tenant_id: NonBlankStr
    recovery_point_at: AwareDatetime
    failure_detected_at: AwareDatetime
    completed_at: AwareDatetime
    source_references: tuple[NonBlankStr, ...]
    unexpected_effect_count: int = Field(ge=0)
    unresolved_effect_count: int = Field(ge=0)
    evidence_reference: NonBlankStr
    blind_replay_permitted: bool = False

    @model_validator(mode="after")
    def validate_gap(self) -> RecoveryGapReconciliation:
        if self.recovery_point_at > self.failure_detected_at:
            raise ValueError("recovery gap cannot begin after failure detection")
        if self.failure_detected_at > self.completed_at:
            raise ValueError("recovery-gap reconciliation cannot complete before failure detection")
        if not self.source_references:
            raise ValueError("recovery-gap reconciliation requires independent source evidence")
        if len(self.source_references) != len(set(self.source_references)):
            raise ValueError("recovery-gap source references must be unique")
        if self.blind_replay_permitted:
            raise ValueError("recovery-gap evidence cannot authorize blind replay")
        return self


class RestoreResult(ContractModel):
    schema_name: str = "prodkit.restore-result"
    schema_version: str = "1.0.0"
    restore_id: UUID
    tenant_id: NonBlankStr
    backup_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    status: RestoreStatus
    actual_rpo_seconds: float = Field(ge=0)
    actual_rto_seconds: float = Field(ge=0)
    integrity_scan_id: UUID
    recovery_gap_reconciliation_id: UUID
    recovery_gap_reconciled: bool
    uncertain_recoveries: tuple[UncertainExecutionRecovery, ...] = ()
    promoted: bool = False
    completed_by: ActorRef

    @model_validator(mode="after")
    def validate_result(self) -> RestoreResult:
        if self.completed_at < self.started_at:
            raise ValueError("restore completion cannot precede start")
        if self.completed_by.tenant_id != self.tenant_id:
            raise ValueError("restore completer must belong to the tenant")
        if any(item.tenant_id != self.tenant_id for item in self.uncertain_recoveries):
            raise ValueError("uncertain recovery crossed tenant boundary")
        unresolved = any(
            item.disposition
            in {
                UncertainRecoveryDisposition.RECONCILE_REQUIRED,
                UncertainRecoveryDisposition.UNVERIFIABLE,
            }
            for item in self.uncertain_recoveries
        )
        if self.status is RestoreStatus.VERIFIED and (unresolved or not self.recovery_gap_reconciled):
            raise ValueError(
                "verified restore requires all known uncertainty and the RPO recovery gap to be reconciled"
            )
        if self.promoted and (
            self.status is not RestoreStatus.VERIFIED or not self.recovery_gap_reconciled
        ):
            raise ValueError("only a fully reconciled verified restore may be promoted")
        return self


class BreakGlassGrant(ContractModel):
    schema_name: str = "prodkit.break-glass-grant"
    schema_version: str = "1.0.0"
    grant_id: UUID
    tenant_id: NonBlankStr
    operator: ActorRef
    approved_by: ActorRef
    capabilities: tuple[BreakGlassCapability, ...]
    reason: NonBlankStr
    ticket_reference: NonBlankStr
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_grant(self) -> BreakGlassGrant:
        if (
            self.operator.tenant_id != self.tenant_id
            or self.approved_by.tenant_id != self.tenant_id
        ):
            raise ValueError("break-glass actors must belong to the tenant")
        if self.operator.kind == self.approved_by.kind and self.operator.id == self.approved_by.id:
            raise ValueError("break-glass use requires an independent approver")
        if not self.capabilities or len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("break-glass capabilities must be non-empty and unique")
        if self.expires_at <= self.issued_at:
            raise ValueError("break-glass grant must expire after issuance")
        return self


class BreakGlassUse(ContractModel):
    schema_name: str = "prodkit.break-glass-use"
    schema_version: str = "1.0.0"
    use_id: UUID
    grant_id: UUID
    tenant_id: NonBlankStr
    capability: BreakGlassCapability
    actor: ActorRef
    occurred_at: AwareDatetime
    purpose: NonBlankStr

    @model_validator(mode="after")
    def validate_use(self) -> BreakGlassUse:
        if self.actor.tenant_id != self.tenant_id:
            raise ValueError("break-glass use actor must belong to the tenant")
        return self


class GameDayExercise(ContractModel):
    schema_name: str = "prodkit.dr-game-day"
    schema_version: str = "1.0.0"
    exercise_id: UUID
    tenant_id: NonBlankStr
    profile_id: NonBlankStr
    profile_revision: int = Field(ge=1)
    backup_id: UUID
    restore_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    simulated_site_failure: bool
    achieved_rpo_seconds: float = Field(ge=0)
    achieved_rto_seconds: float = Field(ge=0)
    chain_verified: bool
    checkpoint_verified: bool
    trust_anchor_verified: bool
    object_store_verified: bool
    uncertain_actions_reconciled: bool
    recovery_gap_reconciled: bool
    durable_catalog_verified: bool
    blind_replay_count: int = Field(default=0, ge=0)
    passed: bool
    notes: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def validate_exercise(self) -> GameDayExercise:
        if self.completed_at < self.started_at:
            raise ValueError("game day completion cannot precede start")
        if self.passed and (
            not self.simulated_site_failure
            or not self.chain_verified
            or not self.checkpoint_verified
            or not self.trust_anchor_verified
            or not self.object_store_verified
            or not self.uncertain_actions_reconciled
            or not self.recovery_gap_reconciled
            or not self.durable_catalog_verified
            or self.blind_replay_count != 0
        ):
            raise ValueError(
                "passing game day requires durable verified recovery with gap reconciliation and no blind replay"
            )
        return self


class RecoveryAuditEvent(ContractModel):
    schema_name: str = "prodkit.recovery-audit-event"
    schema_version: str = "1.0.0"
    event_id: UUID
    tenant_id: NonBlankStr
    event_type: RecoveryAuditEventType
    actor: ActorRef
    occurred_at: AwareDatetime
    target_id: NonBlankStr
    reason: NonBlankStr
    ticket_reference: NonBlankStr | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
