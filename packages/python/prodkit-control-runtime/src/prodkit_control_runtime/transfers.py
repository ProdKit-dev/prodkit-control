from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from prodkit_control_core import (
    AssuranceProfile,
    CompatibilityPolicy,
    EvidenceTransferManifest,
    EvidenceTransferVerification,
    IntegrityViolationError,
    TrustRootPolicy,
    canonical_json_bytes,
    sha256_hex,
)

from .portable import PortableEvidencePackageVerifier, portable_package_sha256


class GovernanceEvidenceTransferVerifier:
    """Verify a governed evidence transfer without trusting a live ProdKit service."""

    def __init__(self, verifier: PortableEvidencePackageVerifier | None = None) -> None:
        self._verifier = verifier or PortableEvidencePackageVerifier()

    def verify(
        self,
        *,
        manifest: EvidenceTransferManifest,
        package: Path,
        compatibility: CompatibilityPolicy,
        trusted_policy: TrustRootPolicy | None = None,
        expected_trust_policy_sha256: str | None = None,
        assurance_profile: AssuranceProfile | None = None,
    ) -> EvidenceTransferVerification:
        package_digest = portable_package_sha256(package)
        if package_digest != manifest.archive_sha256:
            raise IntegrityViolationError("transfer package digest does not match export manifest")
        compatibility.path_from(manifest.source_schema_version)
        verified = self._verifier.verify(
            package,
            trusted_policy=trusted_policy,
            expected_trust_policy_sha256=expected_trust_policy_sha256,
            expected_package_sha256=package_digest,
            assurance_profile=assurance_profile,
        )
        package_manifest = verified["package_manifest"]
        evidence_manifest = verified["evidence_manifest"]
        if not isinstance(package_manifest, dict) or not isinstance(evidence_manifest, dict):
            raise IntegrityViolationError(
                "portable verifier returned malformed verification evidence"
            )
        if package_manifest.get("tenant_id") != manifest.tenant_id:
            raise IntegrityViolationError("transfer tenant does not match portable package")
        bundle_manifest_digest = sha256_hex(canonical_json_bytes(evidence_manifest))
        if bundle_manifest_digest != manifest.bundle_manifest_sha256:
            raise IntegrityViolationError("transfer bundle manifest digest does not match package")
        if trusted_policy is not None:
            trust_anchor_digest = sha256_hex(canonical_json_bytes(trusted_policy))
        elif expected_trust_policy_sha256 is not None:
            trust_anchor_digest = expected_trust_policy_sha256.lower()
        else:  # PortableEvidencePackageVerifier fails first; keep this boundary explicit.
            raise IntegrityViolationError(
                "transfer verification requires an independent trust anchor"
            )
        return EvidenceTransferVerification(
            verification_id=uuid4(),
            transfer_id=manifest.transfer_id,
            tenant_id=manifest.tenant_id,
            verified_at=datetime.now(UTC),
            source_control_version=manifest.source_control_version,
            source_schema_version=manifest.source_schema_version,
            package_sha256=package_digest,
            bundle_manifest_sha256=bundle_manifest_digest,
            trust_anchor_sha256=trust_anchor_digest,
        )
