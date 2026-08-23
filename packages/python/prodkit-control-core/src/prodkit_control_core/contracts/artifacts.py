from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, model_validator

from .base import ContractModel, NonBlankStr, Sha256


class ContentStorageMode(StrEnum):
    NONE = "none"
    HASH_ONLY = "hash_only"
    REDACTED = "redacted"
    FULL = "full"


class ArtifactRef(ContractModel):
    tenant_id: NonBlankStr
    artifact_id: NonBlankStr
    media_type: NonBlankStr
    sha256: Sha256
    size_bytes: int
    storage_mode: ContentStorageMode
    location: NonBlankStr | None = None
    encrypted: bool = False
    redacted: bool = False
    redaction_version: NonBlankStr | None = None
    retention_until: AwareDatetime | None = None
    classification: NonBlankStr = "internal"

    @model_validator(mode="after")
    def validate_storage(self) -> ArtifactRef:
        if self.size_bytes < 0:
            raise ValueError("artifact size cannot be negative")
        if (
            self.storage_mode in {ContentStorageMode.NONE, ContentStorageMode.HASH_ONLY}
            and self.location
        ):
            raise ValueError("none/hash_only artifacts cannot include a storage location")
        if (
            self.storage_mode in {ContentStorageMode.REDACTED, ContentStorageMode.FULL}
            and not self.location
        ):
            raise ValueError("redacted/full artifacts require a storage location")
        if self.redacted and not self.redaction_version:
            raise ValueError("redacted artifacts require a redaction version")
        return self
