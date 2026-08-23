from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .base import ContractModel, NonBlankStr, Sha256

IN_TOTO_STATEMENT_V1 = "https://in-toto.io/Statement/v1"
SLSA_PROVENANCE_V1 = "https://slsa.dev/provenance/v1"
PRODKIT_EVIDENCE_PREDICATE_V1 = "https://schemas.prodkit.dev/control/evidence/v1"


class InteroperabilityModel(BaseModel):
    """Forward-compatible model for externally versioned interoperability contracts."""

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        validate_default=True,
        populate_by_name=True,
    )


class AttestationResourceDescriptor(InteroperabilityModel):
    name: NonBlankStr | None = None
    uri: NonBlankStr | None = None
    digest: dict[NonBlankStr, NonBlankStr] = Field(default_factory=dict)
    download_location: NonBlankStr | None = Field(default=None, alias="downloadLocation")
    media_type: NonBlankStr | None = Field(default=None, alias="mediaType")
    annotations: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self) -> AttestationResourceDescriptor:
        if not self.digest:
            raise ValueError("attestation resources require at least one immutable digest")
        return self


class InTotoStatementV1(InteroperabilityModel):
    type_: Literal["https://in-toto.io/Statement/v1"] = Field(
        default=IN_TOTO_STATEMENT_V1,
        alias="_type",
    )
    subject: tuple[AttestationResourceDescriptor, ...]
    predicate_type: NonBlankStr = Field(alias="predicateType")
    predicate: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_subjects(self) -> InTotoStatementV1:
        if not self.subject:
            raise ValueError("in-toto statements require at least one subject")
        return self


class SlsaBuildDefinitionV1(InteroperabilityModel):
    build_type: NonBlankStr = Field(alias="buildType")
    external_parameters: dict[str, Any] = Field(default_factory=dict, alias="externalParameters")
    internal_parameters: dict[str, Any] = Field(default_factory=dict, alias="internalParameters")
    resolved_dependencies: tuple[AttestationResourceDescriptor, ...] = Field(
        default=(), alias="resolvedDependencies"
    )


class SlsaBuilderV1(InteroperabilityModel):
    id: NonBlankStr
    version: dict[NonBlankStr, NonBlankStr] = Field(default_factory=dict)
    builder_dependencies: tuple[AttestationResourceDescriptor, ...] = Field(
        default=(), alias="builderDependencies"
    )


class SlsaBuildMetadataV1(InteroperabilityModel):
    invocation_id: NonBlankStr | None = Field(default=None, alias="invocationId")
    started_on: AwareDatetime | None = Field(default=None, alias="startedOn")
    finished_on: AwareDatetime | None = Field(default=None, alias="finishedOn")

    @model_validator(mode="after")
    def validate_times(self) -> SlsaBuildMetadataV1:
        if (
            self.started_on is not None
            and self.finished_on is not None
            and self.finished_on < self.started_on
        ):
            raise ValueError("SLSA build finishedOn cannot precede startedOn")
        return self


class SlsaRunDetailsV1(InteroperabilityModel):
    builder: SlsaBuilderV1
    metadata: SlsaBuildMetadataV1 = Field(default_factory=SlsaBuildMetadataV1)
    byproducts: tuple[AttestationResourceDescriptor, ...] = ()


class SlsaProvenancePredicateV1(InteroperabilityModel):
    build_definition: SlsaBuildDefinitionV1 = Field(alias="buildDefinition")
    run_details: SlsaRunDetailsV1 = Field(alias="runDetails")


class ProdKitEvidencePredicateV1(InteroperabilityModel):
    schema_name: Literal["prodkit.control-evidence-attestation"] = (
        "prodkit.control-evidence-attestation"
    )
    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: UUID
    tenant_id: NonBlankStr
    bundle_manifest_sha256: Sha256
    final_event_hash: Sha256
    canonicalization: Literal["prodkit-json-v1"] = "prodkit-json-v1"


class CheckpointSigningAlgorithm(StrEnum):
    ED25519 = "ed25519"


class SigningRequirement(StrEnum):
    OPTIONAL = "optional"
    REQUIRED = "required"


class RetentionLockMode(StrEnum):
    LOGICAL = "logical"
    GOVERNANCE = "governance"
    COMPLIANCE = "compliance"
    WRITE_ONCE = "write_once"


class TrustedSigningKey(ContractModel):
    key_id: NonBlankStr
    algorithm: CheckpointSigningAlgorithm = CheckpointSigningAlgorithm.ED25519
    public_key_base64: NonBlankStr
    signer_id: NonBlankStr
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_key(self) -> TrustedSigningKey:
        try:
            raw = base64.b64decode(self.public_key_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("trusted signing key must contain strict base64") from exc
        if self.algorithm is CheckpointSigningAlgorithm.ED25519 and len(raw) != 32:
            raise ValueError("Ed25519 public keys must be exactly 32 bytes")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("trusted signing key valid_until must follow valid_from")
        if self.revoked_at is not None and self.revoked_at < self.valid_from:
            raise ValueError("trusted signing key cannot be revoked before becoming valid")
        return self


class TrustRootPolicy(ContractModel):
    schema_name: Literal["prodkit.trust-root-policy"] = "prodkit.trust-root-policy"
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonBlankStr
    revision: NonBlankStr
    trusted_keys: tuple[TrustedSigningKey, ...]
    allowed_signers: tuple[NonBlankStr, ...] = ()
    allow_historical_signatures_before_revocation: bool = True

    @model_validator(mode="after")
    def validate_keys(self) -> TrustRootPolicy:
        if not self.trusted_keys:
            raise ValueError("trust-root policy requires at least one trusted key")
        key_ids = tuple(item.key_id for item in self.trusted_keys)
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("trust-root policy key ids must be unique")
        return self


class AssuranceProfile(ContractModel):
    schema_name: Literal["prodkit.assurance-profile"] = "prodkit.assurance-profile"
    schema_version: Literal["1.0.0"] = "1.0.0"
    profile_id: NonBlankStr
    signing: SigningRequirement = SigningRequirement.REQUIRED
    require_retention_lock: bool = True
    minimum_retention_days: int = Field(default=30, ge=1)
    accepted_retention_modes: tuple[RetentionLockMode, ...] = (
        RetentionLockMode.COMPLIANCE,
        RetentionLockMode.WRITE_ONCE,
    )


class SignedCheckpoint(ContractModel):
    schema_name: Literal["prodkit.signed-checkpoint"] = "prodkit.signed-checkpoint"
    schema_version: Literal["1.0.0"] = "1.0.0"
    checkpoint_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    created_at: AwareDatetime
    sequence: int = Field(ge=1)
    final_event_hash: Sha256
    evidence_bundle_sha256: Sha256
    attestation_sha256: Sha256 | None = None
    previous_checkpoint_sha256: Sha256 | None = None
    signer_id: NonBlankStr
    key_id: NonBlankStr
    algorithm: CheckpointSigningAlgorithm = CheckpointSigningAlgorithm.ED25519
    signature_base64: NonBlankStr

    @model_validator(mode="after")
    def validate_signature_encoding(self) -> SignedCheckpoint:
        try:
            raw = base64.b64decode(self.signature_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("checkpoint signature must contain strict base64") from exc
        if self.algorithm is CheckpointSigningAlgorithm.ED25519 and len(raw) != 64:
            raise ValueError("Ed25519 signatures must be exactly 64 bytes")
        return self

    def signing_material(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"signature_base64"})


class RetentionLockReceipt(ContractModel):
    schema_name: Literal["prodkit.retention-lock-receipt"] = "prodkit.retention-lock-receipt"
    schema_version: Literal["1.0.0"] = "1.0.0"
    object_sha256: Sha256
    locked_at: AwareDatetime
    retain_until: AwareDatetime
    mode: RetentionLockMode
    provider: NonBlankStr
    provider_reference: NonBlankStr
    immutable: bool = True

    @model_validator(mode="after")
    def validate_retention(self) -> RetentionLockReceipt:
        if self.retain_until <= self.locked_at:
            raise ValueError("retention lock must expire after it is established")
        if not self.immutable:
            raise ValueError("retention lock receipts must attest immutable retention")
        return self
