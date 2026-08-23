from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from prodkit_control_core import (
    IntegrityViolationError,
    RetentionLockMode,
    RetentionLockReceipt,
    TrustRootPolicy,
    canonical_json_bytes,
    sha256_hex,
)
from prodkit_control_runtime import (
    Ed25519CheckpointSigner,
    PortableAttestationBuilder,
    PortableEvidencePackageBuilder,
    PortableEvidencePackageVerifier,
    attestation_sha256,
    evidence_bundle_sha256,
    portable_package_sha256,
)


def _evidence_archive(tmp_path: Path) -> tuple[Path, dict[str, object], bytes]:
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
    archive = tmp_path / "evidence.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("events.jsonl", events_bytes)
    return archive, manifest, manifest_bytes


def _package(tmp_path: Path):
    evidence, manifest, manifest_bytes = _evidence_archive(tmp_path)
    now = datetime.now(UTC)
    run_id = UUID(str(manifest["run_id"]))
    evidence_digest = evidence_bundle_sha256(evidence)
    statement = PortableAttestationBuilder().evidence_statement(
        run_id=run_id,
        tenant_id="tenant-a",
        bundle_name=evidence.name,
        bundle_sha256=evidence_digest,
        bundle_manifest_sha256=sha256_hex(manifest_bytes),
        final_event_hash=str(manifest["final_event_hash"]),
    )
    signer = Ed25519CheckpointSigner.generate(
        key_id="portable-release-key",
        signer_id="portable-release-service",
    )
    checkpoint = signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=now,
        sequence=1,
        final_event_hash=str(manifest["final_event_hash"]),
        evidence_bundle_sha256=evidence_digest,
        attestation_sha256=attestation_sha256(statement),
    )
    trust = TrustRootPolicy(
        policy_id="portable-trust",
        revision="1",
        trusted_keys=(signer.trusted_key(valid_from=now - timedelta(days=1)),),
        allowed_signers=("portable-release-service",),
    )
    retention = RetentionLockReceipt(
        object_sha256=evidence_digest,
        locked_at=now,
        retain_until=now + timedelta(days=365),
        mode=RetentionLockMode.COMPLIANCE,
        provider="fixture-object-lock",
        provider_reference="lock://portable/1",
    )
    package = PortableEvidencePackageBuilder().build(
        evidence_archive=evidence,
        statement=statement,
        checkpoint=checkpoint,
        trust_policy=trust,
        retention_receipt=retention,
        destination=tmp_path / "portable-evidence.pka",
    )
    return package, trust, run_id


def test_portable_package_roundtrip_with_independent_trust_policy(tmp_path: Path) -> None:
    package, trust, run_id = _package(tmp_path)
    package_digest = portable_package_sha256(package)
    result = PortableEvidencePackageVerifier().verify(
        package,
        trusted_policy=trust,
        expected_package_sha256=package_digest,
    )
    package_manifest = result["package_manifest"]
    evidence_manifest = result["evidence_manifest"]
    assert package_manifest["run_id"] == str(run_id)
    assert evidence_manifest["run_id"] == str(run_id)


def test_portable_package_accepts_independent_trust_digest_anchor(tmp_path: Path) -> None:
    package, trust, _ = _package(tmp_path)
    trust_digest = sha256_hex(canonical_json_bytes(trust))
    result = PortableEvidencePackageVerifier().verify(
        package,
        expected_trust_policy_sha256=trust_digest,
    )
    assert result["package_manifest"]["trust_root_sha256"] == trust_digest


def test_portable_package_refuses_embedded_trust_without_external_anchor(tmp_path: Path) -> None:
    package, _, _ = _package(tmp_path)
    with pytest.raises(IntegrityViolationError, match="independent trust-root"):
        PortableEvidencePackageVerifier().verify(package)


def test_portable_package_member_tampering_is_detected(tmp_path: Path) -> None:
    package, trust, _ = _package(tmp_path)
    tampered = tmp_path / "tampered.pka"
    with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "attestation.json":
                payload += b" "
            target.writestr(info.filename, payload)
    with pytest.raises(IntegrityViolationError, match="member digest mismatch"):
        PortableEvidencePackageVerifier().verify(tampered, trusted_policy=trust)
