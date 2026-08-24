from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import ContractModel, NonBlankStr, Sha256


class EvidenceTransferVerification(ContractModel):
    """Portable evidence verification result bound to an exact transfer payload and trust anchor."""

    schema_name: str = "prodkit.evidence-transfer-verification"
    schema_version: str = "1.0.0"
    verification_id: UUID
    transfer_id: UUID
    tenant_id: NonBlankStr
    verified_at: AwareDatetime
    source_control_version: NonBlankStr
    source_schema_version: int = Field(ge=1)
    package_sha256: Sha256
    bundle_manifest_sha256: Sha256
    trust_anchor_sha256: Sha256
    verified_offline: bool = True

    @model_validator(mode="after")
    def require_offline_verification(self) -> EvidenceTransferVerification:
        if not self.verified_offline:
            raise ValueError("evidence transfer verification must be offline verified")
        return self
