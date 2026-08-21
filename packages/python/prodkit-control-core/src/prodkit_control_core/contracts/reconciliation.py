from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field, PositiveInt, model_validator

from .artifacts import ArtifactRef
from .base import ContractModel, NonBlankStr, Sha256
from .verification import ReconciliationFinding


class ReconciliationSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReconciliationSourceHealth(StrEnum):
    HEALTHY = "healthy"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    CONFLICTING = "conflicting"


class ExpectedExternalAction(ContractModel):
    action_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    source_system: NonBlankStr
    expected_state_digest: Sha256 | None = None
    external_reference: NonBlankStr | None = None


class ExternalStateObservation(ContractModel):
    observation_id: NonBlankStr
    tenant_id: NonBlankStr
    source_system: NonBlankStr
    observed_at: AwareDatetime
    state_digest: Sha256
    action_id: UUID | None = None
    run_id: UUID | None = None
    external_reference: NonBlankStr | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ArtifactRef, ...] = ()


class ExternalAuditEvent(ContractModel):
    event_id: NonBlankStr
    tenant_id: NonBlankStr
    source_system: NonBlankStr
    event_type: NonBlankStr
    occurred_at: AwareDatetime
    payload_digest: Sha256
    actor: NonBlankStr | None = None
    resource: NonBlankStr | None = None
    action_id: UUID | None = None
    run_id: UUID | None = None
    external_reference: NonBlankStr | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ArtifactRef, ...] = ()


class ReconciliationCursor(ContractModel):
    tenant_id: NonBlankStr
    source_system: NonBlankStr
    cursor: NonBlankStr | None = None
    high_watermark: AwareDatetime | None = None
    health: ReconciliationSourceHealth = ReconciliationSourceHealth.HEALTHY
    consecutive_failures: int = Field(default=0, ge=0)
    next_attempt_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class ReconciliationSourceConfig(ContractModel):
    source_system: NonBlankStr
    enabled: bool = True
    poll_interval_seconds: PositiveInt = 300
    stale_after_seconds: PositiveInt = 900
    base_backoff_seconds: PositiveInt = 30
    max_backoff_seconds: PositiveInt = 1800

    @model_validator(mode="after")
    def validate_backoff(self) -> ReconciliationSourceConfig:
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("base backoff cannot exceed maximum backoff")
        return self

    def backoff_for(self, failures: int) -> timedelta:
        if failures <= 0:
            return timedelta(seconds=self.poll_interval_seconds)
        seconds = min(
            self.max_backoff_seconds,
            self.base_backoff_seconds * (2 ** min(failures - 1, 30)),
        )
        return timedelta(seconds=seconds)


class ProductionCompletenessProfile(ContractModel):
    profile_id: NonBlankStr
    tenant_id: NonBlankStr
    organization_id: NonBlankStr | None = None
    required_sources: tuple[NonBlankStr, ...]
    max_source_age_seconds: PositiveInt = 900
    require_matched_reconciliation: bool = True

    @model_validator(mode="after")
    def validate_sources(self) -> ProductionCompletenessProfile:
        if not self.required_sources:
            raise ValueError("production completeness profile requires at least one source")
        if len(set(self.required_sources)) != len(self.required_sources):
            raise ValueError("production completeness sources must be unique")
        return self


class ReconciliationBatch(ContractModel):
    tenant_id: NonBlankStr
    source_system: NonBlankStr
    collected_at: AwareDatetime
    health: ReconciliationSourceHealth
    cursor: NonBlankStr | None = None
    high_watermark: AwareDatetime | None = None
    observations: tuple[ExternalStateObservation, ...] = ()
    audit_events: tuple[ExternalAuditEvent, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> ReconciliationBatch:
        for observation in self.observations:
            if observation.tenant_id != self.tenant_id or observation.source_system != self.source_system:
                raise ValueError("observation scope must match reconciliation batch")
        for event in self.audit_events:
            if event.tenant_id != self.tenant_id or event.source_system != self.source_system:
                raise ValueError("audit event scope must match reconciliation batch")
        if self.high_watermark is not None and self.high_watermark > self.collected_at:
            raise ValueError("high watermark cannot be after collection time")
        return self


class ProductionCompletenessAssessment(ContractModel):
    profile_id: NonBlankStr
    tenant_id: NonBlankStr
    organization_id: NonBlankStr | None = None
    assessed_at: AwareDatetime
    complete: bool
    healthy_sources: tuple[NonBlankStr, ...] = ()
    stale_sources: tuple[NonBlankStr, ...] = ()
    unavailable_sources: tuple[NonBlankStr, ...] = ()
    conflicting_sources: tuple[NonBlankStr, ...] = ()
    blocking_findings: tuple[UUID, ...] = ()


class ReconciliationRunResult(ContractModel):
    reconciliation_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    source_system: NonBlankStr
    started_at: AwareDatetime
    completed_at: AwareDatetime
    health: ReconciliationSourceHealth
    cursor: NonBlankStr | None = None
    high_watermark: AwareDatetime | None = None
    findings: tuple[ReconciliationFinding, ...] = ()
