from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, cast

from prodkit_control_core import (
    AssuranceProfile,
    IN_TOTO_STATEMENT_V1,
    SLSA_PROVENANCE_V1,
    InTotoStatementV1,
    IntegrityViolationError,
    RetentionLockReceipt,
    SignedCheckpoint,
    TrustRootPolicy,
    canonical_json_bytes,
    sha256_hex,
)

from .attestations import OfflineAssuranceVerifier, attestation_bytes, checkpoint_sha256
from .bundles import evidence_bundle_sha256

_PACKAGE_SCHEMA_NAME = "prodkit.portable-evidence-package"
_PACKAGE_SCHEMA_VERSION = "1.0.0"
_ALLOWED_MEMBERS = frozenset(
    {
        "package-manifest.json",
        "evidence.zip",
        "attestation.json",
        "checkpoint.json",
        "trust-root.json",
        "retention-lock.json",
    }
)
_MAX_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024


def portable_package_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PortableEvidencePackageBuilder:
    """Build a self-contained evidence package whose trust is externally anchorable."""

    def __init__(self, verifier: OfflineAssuranceVerifier | None = None) -> None:
        self._verifier = verifier or OfflineAssuranceVerifier()

    def build(
        self,
        *,
        evidence_archive: Path,
        statement: InTotoStatementV1,
        checkpoint: SignedCheckpoint,
        trust_policy: TrustRootPolicy,
        retention_receipt: RetentionLockReceipt,
        destination: Path,
        assurance_profile: AssuranceProfile | None = None,
    ) -> Path:
        if not evidence_archive.is_file():
            raise ValueError("portable package evidence archive must be an existing file")
        profile = assurance_profile or AssuranceProfile(profile_id="prodkit-enterprise-default")
        self._verifier.verify_evidence_archive(
            evidence_archive,
            checkpoint=checkpoint,
            trust_policy=trust_policy,
            statement=statement,
            retention_receipt=retention_receipt,
            assurance_profile=profile,
        )

        evidence_bytes = evidence_archive.read_bytes()
        statement_bytes = attestation_bytes(statement)
        checkpoint_bytes = canonical_json_bytes(checkpoint)
        trust_bytes = canonical_json_bytes(trust_policy)
        retention_bytes = canonical_json_bytes(retention_receipt)
        files = {
            "evidence.zip": sha256_hex(evidence_bytes),
            "attestation.json": sha256_hex(statement_bytes),
            "checkpoint.json": sha256_hex(checkpoint_bytes),
            "trust-root.json": sha256_hex(trust_bytes),
            "retention-lock.json": sha256_hex(retention_bytes),
        }
        manifest = {
            "schema_name": _PACKAGE_SCHEMA_NAME,
            "schema_version": _PACKAGE_SCHEMA_VERSION,
            "run_id": str(checkpoint.run_id),
            "tenant_id": checkpoint.tenant_id,
            "evidence_bundle_sha256": evidence_bundle_sha256(evidence_archive),
            "attestation_sha256": sha256_hex(statement_bytes),
            "checkpoint_sha256": checkpoint_sha256(checkpoint),
            "trust_root_sha256": sha256_hex(trust_bytes),
            "retention_lock_sha256": sha256_hex(retention_bytes),
            "standards": {
                "in_toto_statement": IN_TOTO_STATEMENT_V1,
                "predicate_type": statement.predicate_type,
                "slsa_provenance": (
                    SLSA_PROVENANCE_V1 if statement.predicate_type == SLSA_PROVENANCE_V1 else None
                ),
                "canonicalization": "prodkit-json-v1",
            },
            "files": files,
        }
        manifest_bytes = canonical_json_bytes(manifest)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("package-manifest.json", manifest_bytes)
            archive.writestr("evidence.zip", evidence_bytes)
            archive.writestr("attestation.json", statement_bytes)
            archive.writestr("checkpoint.json", checkpoint_bytes)
            archive.writestr("trust-root.json", trust_bytes)
            archive.writestr("retention-lock.json", retention_bytes)
        return destination


class PortableEvidencePackageVerifier:
    """Verify a portable package offline using an independently trusted policy or digest."""

    def __init__(self, verifier: OfflineAssuranceVerifier | None = None) -> None:
        self._verifier = verifier or OfflineAssuranceVerifier()

    def verify(
        self,
        package: Path,
        *,
        trusted_policy: TrustRootPolicy | None = None,
        expected_trust_policy_sha256: str | None = None,
        expected_package_sha256: str | None = None,
        assurance_profile: AssuranceProfile | None = None,
    ) -> dict[str, object]:
        if expected_package_sha256 is not None:
            actual_package_digest = portable_package_sha256(package)
            if actual_package_digest != expected_package_sha256.lower():
                raise IntegrityViolationError(
                    "portable package does not match independent digest anchor"
                )
        payloads = self._read_package(package)
        manifest = self._load_manifest(payloads["package-manifest.json"])
        self._validate_manifest(manifest, payloads)

        try:
            embedded_trust = TrustRootPolicy.model_validate_json(payloads["trust-root.json"])
            checkpoint = SignedCheckpoint.model_validate_json(payloads["checkpoint.json"])
            retention = RetentionLockReceipt.model_validate_json(payloads["retention-lock.json"])
            statement = InTotoStatementV1.model_validate_json(payloads["attestation.json"])
        except ValueError as exc:
            raise IntegrityViolationError(
                "portable package contains invalid assurance metadata"
            ) from exc

        standards = cast(dict[str, Any], manifest["standards"])
        if standards.get("predicate_type") != statement.predicate_type:
            raise IntegrityViolationError("portable package predicate metadata is inconsistent")
        slsa_marker = standards.get("slsa_provenance")
        if statement.predicate_type == SLSA_PROVENANCE_V1:
            if slsa_marker != SLSA_PROVENANCE_V1:
                raise IntegrityViolationError("portable package SLSA metadata is inconsistent")
        elif slsa_marker is not None:
            raise IntegrityViolationError("portable package falsely declares SLSA provenance")

        embedded_trust_digest = sha256_hex(canonical_json_bytes(embedded_trust))
        if trusted_policy is not None:
            trusted_digest = sha256_hex(canonical_json_bytes(trusted_policy))
            if trusted_digest != embedded_trust_digest:
                raise IntegrityViolationError("embedded trust root does not match trusted policy")
            effective_trust = trusted_policy
        elif expected_trust_policy_sha256 is not None:
            if embedded_trust_digest != expected_trust_policy_sha256.lower():
                raise IntegrityViolationError(
                    "embedded trust root does not match independent anchor"
                )
            effective_trust = embedded_trust
        else:
            raise IntegrityViolationError(
                "portable package verification requires an independent trust-root policy or digest"
            )

        if manifest["trust_root_sha256"] != embedded_trust_digest:
            raise IntegrityViolationError("portable package trust-root digest is inconsistent")

        profile = assurance_profile or AssuranceProfile(profile_id="prodkit-enterprise-default")
        with tempfile.TemporaryDirectory(prefix="prodkit-portable-") as directory:
            evidence_path = Path(directory) / "evidence.zip"
            evidence_path.write_bytes(payloads["evidence.zip"])
            evidence_manifest = self._verifier.verify_evidence_archive(
                evidence_path,
                checkpoint=checkpoint,
                trust_policy=effective_trust,
                statement=statement,
                retention_receipt=retention,
                assurance_profile=profile,
                expected_archive_sha256=str(manifest["evidence_bundle_sha256"]),
            )

        if str(checkpoint.run_id) != manifest["run_id"]:
            raise IntegrityViolationError("portable package checkpoint run id is inconsistent")
        if checkpoint.tenant_id != manifest["tenant_id"]:
            raise IntegrityViolationError("portable package checkpoint tenant is inconsistent")
        return {
            "package_manifest": manifest,
            "evidence_manifest": evidence_manifest,
        }

    @staticmethod
    def _read_package(package: Path) -> dict[str, bytes]:
        try:
            with zipfile.ZipFile(package, "r") as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(names) != len(set(names)):
                    raise IntegrityViolationError("portable package contains duplicate members")
                if set(names) != _ALLOWED_MEMBERS:
                    raise IntegrityViolationError("portable package member set is invalid")
                total = 0
                for info in infos:
                    if info.is_dir() or info.file_size > _MAX_MEMBER_BYTES:
                        raise IntegrityViolationError("portable package contains an invalid member")
                    total += info.file_size
                if total > _MAX_TOTAL_BYTES:
                    raise IntegrityViolationError("portable package exceeds total size limit")
                return {name: archive.read(name) for name in names}
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise IntegrityViolationError("portable package is not a valid archive") from exc

    @staticmethod
    def _load_manifest(payload: bytes) -> dict[str, Any]:
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrityViolationError("portable package manifest is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise IntegrityViolationError("portable package manifest must be an object")
        return cast(dict[str, Any], raw)

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any], payloads: dict[str, bytes]) -> None:
        if manifest.get("schema_name") != _PACKAGE_SCHEMA_NAME:
            raise IntegrityViolationError("portable package schema name is unsupported")
        if manifest.get("schema_version") != _PACKAGE_SCHEMA_VERSION:
            raise IntegrityViolationError("portable package schema version is unsupported")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != _ALLOWED_MEMBERS - {
            "package-manifest.json"
        }:
            raise IntegrityViolationError("portable package file manifest is invalid")
        for name, expected_digest in files.items():
            if not isinstance(expected_digest, str) or len(expected_digest) != 64:
                raise IntegrityViolationError("portable package contains an invalid file digest")
            if any(character not in "0123456789abcdef" for character in expected_digest):
                raise IntegrityViolationError(
                    "portable package contains a non-canonical file digest"
                )
            if sha256_hex(payloads[name]) != expected_digest:
                raise IntegrityViolationError(f"portable package member digest mismatch: {name}")

        scalar_digests = {
            "evidence_bundle_sha256": "evidence.zip",
            "attestation_sha256": "attestation.json",
            "checkpoint_sha256": "checkpoint.json",
            "trust_root_sha256": "trust-root.json",
            "retention_lock_sha256": "retention-lock.json",
        }
        for field, member in scalar_digests.items():
            if manifest.get(field) != files[member]:
                raise IntegrityViolationError(f"portable package {field} is inconsistent")

        standards = manifest.get("standards")
        if not isinstance(standards, dict):
            raise IntegrityViolationError("portable package standards metadata is invalid")
        if standards.get("in_toto_statement") != IN_TOTO_STATEMENT_V1:
            raise IntegrityViolationError(
                "portable package in-toto statement version is unsupported"
            )
        if standards.get("canonicalization") != "prodkit-json-v1":
            raise IntegrityViolationError("portable package canonicalization is unsupported")
