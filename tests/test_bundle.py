from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from prodkit_control_core import (
    IntegrityViolationError,
    LineageGraph,
    ProductionObservationNode,
    RunStatus,
    sha256_hex,
)
from prodkit_control_runtime import (
    EvidenceBundleBuilder,
    EvidenceBundleVerifier,
    InMemoryEventLedger,
    RunCoordinator,
    evidence_bundle_sha256,
)


def _minimal_archive(
    tmp_path: Path,
    *,
    manifest_changes: dict[str, object] | None = None,
    event_changes: dict[str, object] | None = None,
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    run_id = str(uuid4())
    event: dict[str, object] = {
        "sequence": 1,
        "run_id": run_id,
        "tenant_id": "tenant-a",
        "event_type": "run.started",
    }
    if event_changes:
        event.update(event_changes)
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
    if manifest_changes:
        manifest.update(manifest_changes)
    archive = tmp_path / f"bundle-{uuid4()}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("events.jsonl", events_bytes)
        for name, payload in (extra_members or {}).items():
            zf.writestr(name, payload)
    return archive


@pytest.mark.asyncio
async def test_evidence_bundle_roundtrip(tmp_path: Path, human) -> None:
    ledger = InMemoryEventLedger()
    coordinator = RunCoordinator(ledger)
    run = await coordinator.start_run(
        tenant_id=human.tenant_id,
        initiated_by=human,
        environment="test",
        purpose="bundle",
    )
    await coordinator.complete_run(run.run_id, actor=human, status=RunStatus.SUCCEEDED)
    observation = ProductionObservationNode(
        node_id=uuid4(),
        run_id=run.run_id,
        tenant_id=human.tenant_id,
        digest=sha256_hex("state"),
        recorded_at=datetime.now(UTC),
        observation_id=uuid4(),
        environment="production",
        observer_identity="observer",
        observed_at=datetime.now(UTC),
    )
    lineage = LineageGraph(
        run_id=run.run_id,
        tenant_id=human.tenant_id,
        nodes=(observation,),
    )
    archive = await EvidenceBundleBuilder(ledger).build(
        run.run_id,
        tmp_path / "bundle",
        lineage=lineage,
    )
    digest = evidence_bundle_sha256(archive)
    manifest = EvidenceBundleVerifier().verify(
        archive,
        expected_archive_sha256=digest,
    )
    assert manifest["run_id"] == str(run.run_id)
    assert manifest["event_count"] == 2
    assert manifest["lineage_node_count"] == 1
    assert manifest["lineage_relation_count"] == 0

    with pytest.raises(IntegrityViolationError, match="external trust anchor"):
        EvidenceBundleVerifier().verify(archive, expected_archive_sha256="0" * 64)


def test_evidence_bundle_rejects_invalid_zip(tmp_path: Path) -> None:
    archive = tmp_path / "invalid.zip"
    archive.write_bytes(b"not-a-zip")
    with pytest.raises(IntegrityViolationError, match="valid archive"):
        EvidenceBundleVerifier().verify(archive)


def test_evidence_bundle_rejects_invalid_manifest_json(tmp_path: Path) -> None:
    archive = tmp_path / "invalid-manifest.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", b"{")
        zf.writestr("events.jsonl", b"{}\n")
    with pytest.raises(IntegrityViolationError, match="manifest is not valid JSON"):
        EvidenceBundleVerifier().verify(archive)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_name": "other"}, "schema name"),
        ({"schema_version": "2.0.0"}, "schema version"),
        ({"run_id": "not-a-uuid"}, "run id"),
        ({"event_count": 0}, "event_count"),
        ({"counts_by_type": {"run.started": 2}}, "event counts"),
        ({"events_sha256": "0" * 64}, "events digest"),
        ({"final_event_hash": "not-a-digest"}, "final event hash"),
        ({"lineage_node_count": 1}, "declares lineage"),
    ],
)
def test_evidence_bundle_rejects_invalid_manifest_fields(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    archive = _minimal_archive(tmp_path, manifest_changes=changes)
    with pytest.raises(IntegrityViolationError, match=message):
        EvidenceBundleVerifier().verify(archive)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sequence": 2}, "sequence gap"),
        ({"run_id": str(uuid4())}, "run id"),
        ({"tenant_id": ""}, "tenant"),
        ({"event_type": ""}, "event type"),
    ],
)
def test_evidence_bundle_rejects_invalid_event_scope(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    archive = _minimal_archive(tmp_path, event_changes=changes)
    with pytest.raises(IntegrityViolationError, match=message):
        EvidenceBundleVerifier().verify(archive)


def test_evidence_bundle_rejects_count_mismatch_and_unexpected_member(tmp_path: Path) -> None:
    counts_archive = _minimal_archive(
        tmp_path,
        manifest_changes={"counts_by_type": {"other.event": 1}},
    )
    with pytest.raises(IntegrityViolationError, match="event-type counts"):
        EvidenceBundleVerifier().verify(counts_archive)

    unexpected_archive = _minimal_archive(
        tmp_path,
        extra_members={"unexpected.txt": b"not allowed"},
    )
    with pytest.raises(IntegrityViolationError, match="unexpected members"):
        EvidenceBundleVerifier().verify(unexpected_archive)
