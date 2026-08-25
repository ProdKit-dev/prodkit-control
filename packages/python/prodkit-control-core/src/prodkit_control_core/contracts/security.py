from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractModel, NonBlankStr, Sha256


class SecuritySeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    DETECTED = "detected"
    FAILED = "failed"


class IncidentClass(StrEnum):
    CREDENTIAL_COMPROMISE = "credential_compromise"
    IDENTITY_REPLAY = "identity_replay"
    ABUSE_OR_DOS = "abuse_or_dos"
    SUPPLY_CHAIN = "supply_chain"
    DATA_INTEGRITY = "data_integrity"
    CONTROL_PLANE_BYPASS = "control_plane_bypass"
    AVAILABILITY = "availability"


class SecretReference(ContractModel):
    """Opaque reference to secret material. Secret values never belong in this contract."""

    schema_name: Literal["prodkit.secret-reference"] = "prodkit.secret-reference"
    schema_version: Literal["1.0.0"] = "1.0.0"
    provider: NonBlankStr
    reference: NonBlankStr
    version: NonBlankStr | None = None
    tenant_id: NonBlankStr
    purpose: NonBlankStr
    audience: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def validate_reference(self) -> SecretReference:
        lowered = self.reference.lower()
        forbidden = ("password=", "token=", "secret=", "private_key=", "authorization:")
        if any(marker in lowered for marker in forbidden):
            raise ValueError("secret references must not contain inline secret material")
        if len(self.audience) != len(set(self.audience)):
            raise ValueError("secret reference audience values must be unique")
        return self


class WorkloadIdentityPolicy(ContractModel):
    schema_name: Literal["prodkit.workload-identity-policy"] = "prodkit.workload-identity-policy"
    schema_version: Literal["1.0.0"] = "1.0.0"
    issuer: NonBlankStr
    audience: NonBlankStr
    subject_prefixes: tuple[NonBlankStr, ...]
    allowed_client_ids: tuple[NonBlankStr, ...] = ()
    max_assertion_lifetime_seconds: int = Field(default=300, ge=30, le=900)
    clock_skew_seconds: int = Field(default=30, ge=0, le=120)
    require_not_before: bool = True
    require_nonce: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> WorkloadIdentityPolicy:
        if not self.issuer.startswith("https://"):
            raise ValueError("workload identity issuer must use HTTPS")
        if not self.subject_prefixes:
            raise ValueError("workload identity policy requires at least one subject prefix")
        if len(self.subject_prefixes) != len(set(self.subject_prefixes)):
            raise ValueError("workload identity subject prefixes must be unique")
        if len(self.allowed_client_ids) != len(set(self.allowed_client_ids)):
            raise ValueError("workload identity client ids must be unique")
        return self


class WorkloadIdentityAssertion(ContractModel):
    schema_name: Literal["prodkit.workload-identity-assertion"] = (
        "prodkit.workload-identity-assertion"
    )
    schema_version: Literal["1.0.0"] = "1.0.0"
    issuer: NonBlankStr
    subject: NonBlankStr
    audience: NonBlankStr
    tenant_id: NonBlankStr
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    not_before: AwareDatetime | None = None
    nonce: NonBlankStr | None = None
    client_id: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_times(self) -> WorkloadIdentityAssertion:
        if self.expires_at <= self.issued_at:
            raise ValueError("workload assertion expiry must follow issuance")
        if self.not_before is not None and self.not_before > self.expires_at:
            raise ValueError("workload assertion not_before cannot follow expiry")
        return self

    @property
    def lifetime(self) -> timedelta:
        return self.expires_at - self.issued_at


class SecurityAuditEvent(ContractModel):
    schema_name: Literal["prodkit.security-audit-event"] = "prodkit.security-audit-event"
    schema_version: Literal["1.0.0"] = "1.0.0"
    event_id: UUID
    occurred_at: AwareDatetime
    event_type: NonBlankStr
    severity: SecuritySeverity
    outcome: SecurityOutcome
    tenant_id: NonBlankStr | None = None
    principal_id: NonBlankStr | None = None
    action_id: UUID | None = None
    request_id: NonBlankStr | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class RateLimitPolicy(ContractModel):
    schema_name: Literal["prodkit.rate-limit-policy"] = "prodkit.rate-limit-policy"
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonBlankStr
    limit: int = Field(ge=1, le=1_000_000)
    window_seconds: int = Field(ge=1, le=86_400)
    burst: int = Field(default=0, ge=0, le=1_000_000)
    max_keys: int = Field(default=100_000, ge=1, le=10_000_000)


class ArtifactProvenancePolicy(ContractModel):
    schema_name: Literal["prodkit.artifact-provenance-policy"] = (
        "prodkit.artifact-provenance-policy"
    )
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonBlankStr
    required_predicate_type: NonBlankStr = "https://slsa.dev/provenance/v1"
    allowed_builder_ids: tuple[NonBlankStr, ...]
    allowed_build_types: tuple[NonBlankStr, ...] = ()
    require_verified_signature: bool = True

    @model_validator(mode="after")
    def validate_builders(self) -> ArtifactProvenancePolicy:
        if not self.allowed_builder_ids:
            raise ValueError("provenance policy requires at least one trusted builder")
        if len(self.allowed_builder_ids) != len(set(self.allowed_builder_ids)):
            raise ValueError("provenance builder ids must be unique")
        if len(self.allowed_build_types) != len(set(self.allowed_build_types)):
            raise ValueError("provenance build types must be unique")
        return self


class OperationalSLO(ContractModel):
    schema_name: Literal["prodkit.operational-slo"] = "prodkit.operational-slo"
    schema_version: Literal["1.0.0"] = "1.0.0"
    slo_id: NonBlankStr
    metric: NonBlankStr
    target_ratio: float = Field(gt=0.0, le=1.0)
    window_seconds: int = Field(ge=60, le=31_536_000)
    page_burn_rate: float = Field(default=14.4, gt=0.0)
    ticket_burn_rate: float = Field(default=6.0, gt=0.0)

    @model_validator(mode="after")
    def validate_burn_rates(self) -> OperationalSLO:
        if self.page_burn_rate <= self.ticket_burn_rate:
            raise ValueError("page burn rate must be greater than ticket burn rate")
        return self


class IncidentResponsePolicy(ContractModel):
    schema_name: Literal["prodkit.incident-response-policy"] = "prodkit.incident-response-policy"
    schema_version: Literal["1.0.0"] = "1.0.0"
    incident_class: IncidentClass
    owner: NonBlankStr
    severity: SecuritySeverity
    acknowledge_within_seconds: int = Field(ge=60, le=86_400)
    contain_within_seconds: int = Field(ge=60, le=604_800)
    patch_within_seconds: int | None = Field(default=None, ge=60, le=2_592_000)


class SecurityControlEvidence(ContractModel):
    schema_name: Literal["prodkit.security-control-evidence"] = "prodkit.security-control-evidence"
    schema_version: Literal["1.0.0"] = "1.0.0"
    control_id: NonBlankStr
    evidence_sha256: Sha256
    collected_at: AwareDatetime
    source: NonBlankStr
