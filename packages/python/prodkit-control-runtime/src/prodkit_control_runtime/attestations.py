from __future__ import annotations

import base64
import binascii
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from prodkit_control_core import (
    AssuranceProfile,
    AttestationResourceDescriptor,
    CheckpointSigningAlgorithm,
    InTotoStatementV1,
    IntegrityViolationError,
    ProdKitEvidencePredicateV1,
    PRODKIT_EVIDENCE_PREDICATE_V1,
    RetentionLockReceipt,
    SLSA_PROVENANCE_V1,
    SignedCheckpoint,
    SigningRequirement,
    SlsaBuildDefinitionV1,
    SlsaBuilderV1,
    SlsaBuildMetadataV1,
    SlsaProvenancePredicateV1,
    SlsaRunDetailsV1,
    TrustRootPolicy,
    TrustedSigningKey,
    canonical_json_bytes,
    sha256_hex,
)

from .bundles import EvidenceBundleVerifier, evidence_bundle_sha256


def attestation_bytes(statement: InTotoStatementV1) -> bytes:
    """Serialize an in-toto statement using the versioned ProdKit canonical JSON profile."""

    return canonical_json_bytes(
        statement.model_dump(mode="python", by_alias=True, exclude_none=True)
    )


def attestation_sha256(statement: InTotoStatementV1) -> str:
    return sha256_hex(attestation_bytes(statement))


def checkpoint_sha256(checkpoint: SignedCheckpoint) -> str:
    return sha256_hex(canonical_json_bytes(checkpoint))


class PortableAttestationBuilder:
    """Emit provider-neutral in-toto statements and SLSA build-provenance predicates."""

    @staticmethod
    def resource(
        *,
        sha256: str,
        name: str | None = None,
        uri: str | None = None,
        media_type: str | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> AttestationResourceDescriptor:
        return AttestationResourceDescriptor(
            name=name,
            uri=uri,
            digest={"sha256": sha256},
            media_type=media_type,
            annotations=annotations or {},
        )

    def evidence_statement(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        bundle_name: str,
        bundle_sha256: str,
        bundle_manifest_sha256: str,
        final_event_hash: str,
    ) -> InTotoStatementV1:
        predicate = ProdKitEvidencePredicateV1(
            run_id=run_id,
            tenant_id=tenant_id,
            bundle_manifest_sha256=bundle_manifest_sha256,
            final_event_hash=final_event_hash,
        )
        return InTotoStatementV1(
            subject=(self.resource(name=bundle_name, sha256=bundle_sha256),),
            predicate_type=PRODKIT_EVIDENCE_PREDICATE_V1,
            predicate=predicate.model_dump(mode="json", exclude_none=True),
        )

    def slsa_build_provenance_statement(
        self,
        *,
        subjects: tuple[AttestationResourceDescriptor, ...],
        build_type: str,
        builder_id: str,
        external_parameters: dict[str, Any],
        invocation_id: str | None = None,
        started_on: datetime | None = None,
        finished_on: datetime | None = None,
        internal_parameters: dict[str, Any] | None = None,
        resolved_dependencies: tuple[AttestationResourceDescriptor, ...] = (),
        builder_versions: dict[str, str] | None = None,
        builder_dependencies: tuple[AttestationResourceDescriptor, ...] = (),
        byproducts: tuple[AttestationResourceDescriptor, ...] = (),
    ) -> InTotoStatementV1:
        provenance = SlsaProvenancePredicateV1(
            build_definition=SlsaBuildDefinitionV1(
                build_type=build_type,
                external_parameters=external_parameters,
                internal_parameters=internal_parameters or {},
                resolved_dependencies=resolved_dependencies,
            ),
            run_details=SlsaRunDetailsV1(
                builder=SlsaBuilderV1(
                    id=builder_id,
                    version=builder_versions or {},
                    builder_dependencies=builder_dependencies,
                ),
                metadata=SlsaBuildMetadataV1(
                    invocation_id=invocation_id,
                    started_on=started_on,
                    finished_on=finished_on,
                ),
                byproducts=byproducts,
            ),
        )
        return InTotoStatementV1(
            subject=subjects,
            predicate_type=SLSA_PROVENANCE_V1,
            predicate=provenance.model_dump(mode="json", by_alias=True, exclude_none=True),
        )


class Ed25519CheckpointSigner:
    """Standalone-capable signer for deterministic ProdKit evidence checkpoints."""

    def __init__(
        self,
        *,
        key_id: str,
        signer_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        if not key_id or not signer_id:
            raise ValueError("checkpoint signer requires non-empty key and signer ids")
        self._key_id = key_id
        self._signer_id = signer_id
        self._private_key = private_key

    @classmethod
    def generate(cls, *, key_id: str, signer_id: str) -> Ed25519CheckpointSigner:
        return cls(
            key_id=key_id,
            signer_id=signer_id,
            private_key=Ed25519PrivateKey.generate(),
        )

    @classmethod
    def from_private_key_bytes(
        cls,
        *,
        key_id: str,
        signer_id: str,
        private_key: bytes,
    ) -> Ed25519CheckpointSigner:
        if len(private_key) != 32:
            raise ValueError("Ed25519 private keys must be exactly 32 bytes")
        return cls(
            key_id=key_id,
            signer_id=signer_id,
            private_key=Ed25519PrivateKey.from_private_bytes(private_key),
        )

    def private_key_bytes(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def trusted_key(
        self,
        *,
        valid_from: datetime,
        valid_until: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> TrustedSigningKey:
        public_key = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return TrustedSigningKey(
            key_id=self._key_id,
            signer_id=self._signer_id,
            public_key_base64=base64.b64encode(public_key).decode("ascii"),
            valid_from=valid_from,
            valid_until=valid_until,
            revoked_at=revoked_at,
        )

    def sign(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        created_at: datetime,
        sequence: int,
        final_event_hash: str,
        evidence_bundle_sha256: str,
        attestation_sha256: str | None = None,
        previous_checkpoint_sha256: str | None = None,
    ) -> SignedCheckpoint:
        checkpoint_id = uuid5(
            NAMESPACE_URL,
            ":".join(
                (
                    "prodkit-control-checkpoint-v1",
                    str(run_id),
                    str(sequence),
                    evidence_bundle_sha256,
                    attestation_sha256 or "none",
                    self._signer_id,
                    self._key_id,
                )
            ),
        )
        signing_material = {
            "schema_name": "prodkit.signed-checkpoint",
            "schema_version": "1.0.0",
            "checkpoint_id": checkpoint_id,
            "run_id": run_id,
            "tenant_id": tenant_id,
            "created_at": created_at,
            "sequence": sequence,
            "final_event_hash": final_event_hash,
            "evidence_bundle_sha256": evidence_bundle_sha256,
            "attestation_sha256": attestation_sha256,
            "previous_checkpoint_sha256": previous_checkpoint_sha256,
            "signer_id": self._signer_id,
            "key_id": self._key_id,
            "algorithm": CheckpointSigningAlgorithm.ED25519,
        }
        signature = self._private_key.sign(canonical_json_bytes(signing_material))
        return SignedCheckpoint(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            tenant_id=tenant_id,
            created_at=created_at,
            sequence=sequence,
            final_event_hash=final_event_hash,
            evidence_bundle_sha256=evidence_bundle_sha256,
            attestation_sha256=attestation_sha256,
            previous_checkpoint_sha256=previous_checkpoint_sha256,
            signer_id=self._signer_id,
            key_id=self._key_id,
            algorithm=CheckpointSigningAlgorithm.ED25519,
            signature_base64=base64.b64encode(signature).decode("ascii"),
        )


class OfflineAssuranceVerifier:
    """Verify portable evidence without network access or a ProdKit control-plane service."""

    def __init__(self, bundle_verifier: EvidenceBundleVerifier | None = None) -> None:
        self._bundle_verifier = bundle_verifier or EvidenceBundleVerifier()

    @staticmethod
    def parse_statement(payload: bytes | str) -> InTotoStatementV1:
        try:
            return InTotoStatementV1.model_validate_json(payload)
        except ValueError as exc:
            raise IntegrityViolationError(
                "attestation is not a supported in-toto Statement v1"
            ) from exc

    @staticmethod
    def verify_statement_subject(
        statement: InTotoStatementV1,
        *,
        expected_sha256: str,
    ) -> None:
        if not any(
            subject.digest.get("sha256") == expected_sha256 for subject in statement.subject
        ):
            raise IntegrityViolationError(
                "attestation does not identify the expected subject digest"
            )

    @staticmethod
    def verify_checkpoint(
        checkpoint: SignedCheckpoint,
        *,
        trust_policy: TrustRootPolicy,
    ) -> TrustedSigningKey:
        matching = tuple(
            key for key in trust_policy.trusted_keys if key.key_id == checkpoint.key_id
        )
        if len(matching) != 1:
            raise IntegrityViolationError("checkpoint signing key is not uniquely trusted")
        key = matching[0]
        if checkpoint.algorithm is not key.algorithm:
            raise IntegrityViolationError("checkpoint signing algorithm does not match trusted key")
        if checkpoint.signer_id != key.signer_id:
            raise IntegrityViolationError("checkpoint signer identity does not match trusted key")
        if (
            trust_policy.allowed_signers
            and checkpoint.signer_id not in trust_policy.allowed_signers
        ):
            raise IntegrityViolationError("checkpoint signer is not allowed by trust-root policy")
        if checkpoint.created_at < key.valid_from:
            raise IntegrityViolationError("checkpoint predates signing-key validity")
        if key.valid_until is not None and checkpoint.created_at > key.valid_until:
            raise IntegrityViolationError("checkpoint postdates signing-key validity")
        if key.revoked_at is not None:
            if not trust_policy.allow_historical_signatures_before_revocation:
                raise IntegrityViolationError("checkpoint signing key has been revoked")
            if checkpoint.created_at >= key.revoked_at:
                raise IntegrityViolationError("checkpoint was created after signing-key revocation")

        try:
            public_bytes = base64.b64decode(key.public_key_base64, validate=True)
            signature = base64.b64decode(checkpoint.signature_base64, validate=True)
        except (ValueError, binascii.Error) as exc:  # pragma: no cover - contracts validate first
            raise IntegrityViolationError("checkpoint signing material is malformed") from exc
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        try:
            public_key.verify(signature, canonical_json_bytes(checkpoint.signing_material()))
        except InvalidSignature as exc:
            raise IntegrityViolationError("checkpoint signature verification failed") from exc
        return key

    @staticmethod
    def verify_retention_lock(
        receipt: RetentionLockReceipt,
        *,
        object_sha256: str,
        profile: AssuranceProfile,
    ) -> None:
        if receipt.object_sha256 != object_sha256:
            raise IntegrityViolationError(
                "retention-lock receipt does not identify evidence object"
            )
        if receipt.mode not in profile.accepted_retention_modes:
            raise IntegrityViolationError(
                "retention-lock mode is not accepted by assurance profile"
            )
        retained_seconds = (receipt.retain_until - receipt.locked_at).total_seconds()
        if retained_seconds < profile.minimum_retention_days * 86_400:
            raise IntegrityViolationError(
                "retention-lock duration is below assurance profile minimum"
            )

    def verify_evidence_archive(
        self,
        archive: Path,
        *,
        checkpoint: SignedCheckpoint | None,
        trust_policy: TrustRootPolicy | None,
        statement: InTotoStatementV1 | None = None,
        retention_receipt: RetentionLockReceipt | None = None,
        assurance_profile: AssuranceProfile | None = None,
        expected_archive_sha256: str | None = None,
    ) -> dict[str, object]:
        profile = assurance_profile or AssuranceProfile(profile_id="prodkit-enterprise-default")
        archive_digest = evidence_bundle_sha256(archive)
        if (
            expected_archive_sha256 is not None
            and archive_digest != expected_archive_sha256.lower()
        ):
            raise IntegrityViolationError(
                "evidence archive does not match independent digest anchor"
            )

        if checkpoint is None:
            if profile.signing is SigningRequirement.REQUIRED:
                raise IntegrityViolationError("assurance profile requires a signed checkpoint")
        else:
            if trust_policy is None:
                raise IntegrityViolationError(
                    "signed checkpoint verification requires a trust-root policy"
                )
            self.verify_checkpoint(checkpoint, trust_policy=trust_policy)
            if checkpoint.evidence_bundle_sha256 != archive_digest:
                raise IntegrityViolationError(
                    "signed checkpoint does not identify evidence archive"
                )

        manifest = self._bundle_verifier.verify(
            archive,
            expected_archive_sha256=archive_digest,
        )
        if checkpoint is not None:
            if str(checkpoint.run_id) != manifest.get("run_id"):
                raise IntegrityViolationError("checkpoint run id does not match evidence bundle")
            if checkpoint.final_event_hash != manifest.get("final_event_hash"):
                raise IntegrityViolationError(
                    "checkpoint final event hash does not match evidence bundle"
                )

        if statement is not None:
            self.verify_statement_subject(statement, expected_sha256=archive_digest)
            statement_digest = attestation_sha256(statement)
            if checkpoint is not None and checkpoint.attestation_sha256 != statement_digest:
                raise IntegrityViolationError(
                    "checkpoint attestation digest does not match statement"
                )

        if profile.require_retention_lock:
            if retention_receipt is None:
                raise IntegrityViolationError("assurance profile requires a retention-lock receipt")
            self.verify_retention_lock(
                retention_receipt,
                object_sha256=archive_digest,
                profile=profile,
            )
        elif retention_receipt is not None:
            self.verify_retention_lock(
                retention_receipt,
                object_sha256=archive_digest,
                profile=profile,
            )

        return manifest
