from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from prodkit_control_core import (
    AssuranceProfile,
    InTotoStatementV1,
    IntegrityViolationError,
    RetentionLockMode,
    RetentionLockReceipt,
    SigningRequirement,
    TrustRootPolicy,
    sha256_hex,
)
from prodkit_control_runtime import (
    Ed25519CheckpointSigner,
    OfflineAssuranceVerifier,
    PortableAttestationBuilder,
    attestation_sha256,
    evidence_bundle_sha256,
)


def _minimal_archive(tmp_path: Path) -> tuple[Path, dict[str, object], bytes]:
    run_id = str(uuid4())
    event: dict[str, object] = {
        "sequence": 1,
        "run_id": run_id,
        "tenant_id": "tenant-a",
        "event_type": "run.started",
    }
    event_hash = sha256_hex({"event": event, "previous_event_hash": None})
    event["integrity"] = {"previous_event_hash": None, "event_hash": event_hash}
    events_bytes = json.dumps(event, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    manifest: dict[str, object] = {
        "schema_name": "prodkit.evidence-bundle-manifest",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "event_count": 1,
        "events_sha256": sha256_hex(events_bytes),
        "final_event_hash": event_hash,
        "counts_by_type": {"run.started": 1},
        "lineage_node_count": 0,
        "lineage_relation_count": 0,
        "files": {"events.jsonl": sha256_hex(events_bytes)},
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    archive = tmp_path / "portable-evidence.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("events.jsonl", events_bytes)
    return archive, manifest, manifest_bytes


def test_in_toto_and_slsa_emission_is_stable_and_forward_compatible() -> None:
    builder = PortableAttestationBuilder()
    subject = builder.resource(name="artifact.whl", sha256="a" * 64)
    statement = builder.slsa_build_provenance_statement(
        subjects=(subject,),
        build_type="https://example.test/build-types/python-wheel/v1",
        builder_id="https://example.test/builders/release",
        external_parameters={"ref": "refs/tags/v0.3.0"},
        invocation_id="build-42",
    )
    payload = statement.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert payload["_type"] == "https://in-toto.io/Statement/v1"
    assert payload["predicateType"] == "https://slsa.dev/provenance/v1"
    predicate = payload["predicate"]
    assert predicate["buildDefinition"]["buildType"].endswith("python-wheel/v1")
    assert predicate["runDetails"]["builder"]["id"].endswith("/builders/release")

    payload["futureStatementField"] = {"safe": True}
    payload["subject"][0]["futureResourceField"] = "ignored"
    reparsed = InTotoStatementV1.model_validate(payload)
    assert reparsed.subject[0].digest["sha256"] == "a" * 64


def test_offline_assurance_verifies_v02_bundle_with_v03_checkpoint(tmp_path: Path) -> None:
    archive, manifest, manifest_bytes = _minimal_archive(tmp_path)
    archive_digest = evidence_bundle_sha256(archive)
    now = datetime.now(UTC)
    run_id = uuid4()
    # The fixture is intentionally a v0.2-era evidence-bundle schema. Reuse its canonical run id.
    from uuid import UUID

    run_id = UUID(str(manifest["run_id"]))
    builder = PortableAttestationBuilder()
    statement = builder.evidence_statement(
        run_id=run_id,
        tenant_id="tenant-a",
        bundle_name=archive.name,
        bundle_sha256=archive_digest,
        bundle_manifest_sha256=sha256_hex(manifest_bytes),
        final_event_hash=str(manifest["final_event_hash"]),
    )

    signer = Ed25519CheckpointSigner.generate(key_id="release-2026q3", signer_id="release-service")
    checkpoint = signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=now,
        sequence=1,
        final_event_hash=str(manifest["final_event_hash"]),
        evidence_bundle_sha256=archive_digest,
        attestation_sha256=attestation_sha256(statement),
    )
    trust = TrustRootPolicy(
        policy_id="release-trust",
        revision="2026-q3",
        trusted_keys=(signer.trusted_key(valid_from=now - timedelta(days=1)),),
        allowed_signers=("release-service",),
    )
    retention = RetentionLockReceipt(
        object_sha256=archive_digest,
        locked_at=now,
        retain_until=now + timedelta(days=365),
        mode=RetentionLockMode.COMPLIANCE,
        provider="fixture-worm-store",
        provider_reference="retention://bundle/42",
    )
    manifest_result = OfflineAssuranceVerifier().verify_evidence_archive(
        archive,
        checkpoint=checkpoint,
        trust_policy=trust,
        statement=statement,
        retention_receipt=retention,
        expected_archive_sha256=archive_digest,
    )
    assert manifest_result["schema_version"] == "1.0.0"
    assert manifest_result["run_id"] == str(run_id)


def test_checkpoint_tampering_and_post_revocation_signing_fail_closed(tmp_path: Path) -> None:
    archive, manifest, _ = _minimal_archive(tmp_path)
    archive_digest = evidence_bundle_sha256(archive)
    now = datetime.now(UTC)
    from uuid import UUID

    run_id = UUID(str(manifest["run_id"]))
    signer = Ed25519CheckpointSigner.generate(key_id="rotating-key", signer_id="release-service")
    key = signer.trusted_key(
        valid_from=now - timedelta(days=30),
        revoked_at=now + timedelta(minutes=1),
    )
    trust = TrustRootPolicy(
        policy_id="rotation-test",
        revision="2",
        trusted_keys=(key,),
    )
    before_revocation = signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=now,
        sequence=1,
        final_event_hash=str(manifest["final_event_hash"]),
        evidence_bundle_sha256=archive_digest,
    )
    OfflineAssuranceVerifier.verify_checkpoint(before_revocation, trust_policy=trust)

    tampered = before_revocation.model_copy(update={"final_event_hash": "0" * 64})
    with pytest.raises(IntegrityViolationError, match="signature verification failed"):
        OfflineAssuranceVerifier.verify_checkpoint(tampered, trust_policy=trust)

    after_revocation = signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=now + timedelta(minutes=2),
        sequence=2,
        final_event_hash=str(manifest["final_event_hash"]),
        evidence_bundle_sha256=archive_digest,
    )
    with pytest.raises(IntegrityViolationError, match="after signing-key revocation"):
        OfflineAssuranceVerifier.verify_checkpoint(after_revocation, trust_policy=trust)


def test_assurance_profile_fails_closed_without_required_controls(tmp_path: Path) -> None:
    archive, _, _ = _minimal_archive(tmp_path)
    verifier = OfflineAssuranceVerifier()
    profile = AssuranceProfile(
        profile_id="unsigned-test",
        signing=SigningRequirement.OPTIONAL,
        require_retention_lock=True,
    )
    with pytest.raises(IntegrityViolationError, match="retention-lock receipt"):
        verifier.verify_evidence_archive(
            archive,
            checkpoint=None,
            trust_policy=None,
            assurance_profile=profile,
        )
