from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from prodkit_control_core import (
    EventLedger,
    IntegrityViolationError,
    LineageGraph,
    canonical_json_bytes,
    sha256_hex,
)

from .projectors import project_run

_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_ALLOWED_MEMBERS = frozenset({"manifest.json", "events.jsonl", "lineage.json"})


def evidence_bundle_sha256(path: Path) -> str:
    """Return the external trust-anchor digest for an evidence archive."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceBundleBuilder:
    def __init__(self, ledger: EventLedger) -> None:
        self._ledger = ledger

    async def build(
        self,
        run_id: UUID,
        destination: Path,
        *,
        lineage: LineageGraph | None = None,
    ) -> Path:
        await self._ledger.verify_run(run_id)
        events = await self._ledger.list_run_events(run_id)
        if not events:
            raise ValueError(f"run {run_id} has no events")
        projection = project_run(events)
        destination.mkdir(parents=True, exist_ok=True)
        events_path = destination / "events.jsonl"
        events_bytes = b"\n".join(canonical_json_bytes(event) for event in events) + b"\n"
        events_path.write_bytes(events_bytes)
        if lineage is not None and lineage.run_id != run_id:
            raise ValueError("lineage graph and evidence bundle run ids must match")
        files = {"events.jsonl": sha256_hex(events_bytes)}
        lineage_bytes = canonical_json_bytes(lineage) if lineage is not None else None
        if lineage_bytes is not None:
            files["lineage.json"] = sha256_hex(lineage_bytes)
        manifest = {
            "schema_name": "prodkit.evidence-bundle-manifest",
            "schema_version": "1.0.0",
            "run_id": str(run_id),
            "generated_at": datetime.now(UTC).isoformat(),
            "event_count": len(events),
            "events_sha256": sha256_hex(events_bytes),
            "final_event_hash": projection.final_event_hash,
            "counts_by_type": projection.counts_by_type,
            "lineage_node_count": len(lineage.nodes) if lineage is not None else 0,
            "lineage_relation_count": len(lineage.relations) if lineage is not None else 0,
            "files": files,
        }
        manifest_bytes = canonical_json_bytes(manifest)
        (destination / "manifest.json").write_bytes(manifest_bytes)
        archive = destination.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(events_path, arcname="events.jsonl")
            if lineage_bytes is not None:
                zf.writestr("lineage.json", lineage_bytes)
            zf.writestr("manifest.json", manifest_bytes)
        return archive


class EvidenceBundleVerifier:
    def verify(
        self,
        archive: Path,
        *,
        expected_archive_sha256: str | None = None,
    ) -> dict[str, object]:
        if expected_archive_sha256 is not None:
            actual_archive_sha256 = evidence_bundle_sha256(archive)
            if actual_archive_sha256 != expected_archive_sha256.lower():
                raise IntegrityViolationError(
                    "evidence bundle archive digest does not match external trust anchor"
                )

        with zipfile.ZipFile(archive, "r") as zf:
            infos = zf.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise IntegrityViolationError("evidence bundle contains duplicate members")
            member_names = set(names)
            if not {"manifest.json", "events.jsonl"}.issubset(member_names):
                raise IntegrityViolationError("evidence bundle is missing required members")
            if member_names - _ALLOWED_MEMBERS:
                raise IntegrityViolationError("evidence bundle contains unexpected members")
            total_size = 0
            for info in infos:
                if info.is_dir() or info.file_size > _MAX_MEMBER_BYTES:
                    raise IntegrityViolationError("evidence bundle contains an invalid member")
                total_size += info.file_size
            if total_size > _MAX_TOTAL_BYTES:
                raise IntegrityViolationError("evidence bundle exceeds total size limit")
            manifest_bytes = zf.read("manifest.json")
            events_bytes = zf.read("events.jsonl")
            lineage_bytes = zf.read("lineage.json") if "lineage.json" in member_names else None

        manifest = json.loads(manifest_bytes)
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise IntegrityViolationError("evidence bundle manifest files are invalid")
        expected_members = {"events.jsonl"}
        if lineage_bytes is not None:
            expected_members.add("lineage.json")
        if set(files) != expected_members:
            raise IntegrityViolationError("evidence bundle manifest members do not match archive")

        expected = files["events.jsonl"]
        actual = sha256_hex(events_bytes)
        if actual != expected or actual != manifest["events_sha256"]:
            raise IntegrityViolationError("evidence bundle events digest does not match manifest")
        lines = [line for line in events_bytes.splitlines() if line]
        if len(lines) != manifest["event_count"]:
            raise IntegrityViolationError("evidence bundle event count does not match manifest")
        previous = None
        final_hash = None
        for index, line in enumerate(lines, start=1):
            event = json.loads(line)
            if event["sequence"] != index:
                raise IntegrityViolationError("evidence bundle contains a sequence gap")
            integrity = event.pop("integrity")
            if integrity["previous_event_hash"] != previous:
                raise IntegrityViolationError("evidence bundle contains a broken previous hash")
            expected_hash = sha256_hex({"event": event, "previous_event_hash": previous})
            if integrity["event_hash"] != expected_hash:
                raise IntegrityViolationError("evidence bundle contains a modified event")
            previous = integrity["event_hash"]
            final_hash = previous
        if final_hash != manifest["final_event_hash"]:
            raise IntegrityViolationError("evidence bundle final hash does not match manifest")

        if lineage_bytes is not None:
            if sha256_hex(lineage_bytes) != files["lineage.json"]:
                raise IntegrityViolationError(
                    "evidence bundle lineage digest does not match manifest"
                )
            lineage = LineageGraph.model_validate_json(lineage_bytes)
            if str(lineage.run_id) != manifest["run_id"]:
                raise IntegrityViolationError(
                    "evidence bundle lineage run id does not match manifest"
                )
            if len(lineage.nodes) != manifest["lineage_node_count"]:
                raise IntegrityViolationError("evidence bundle lineage node count does not match")
            if len(lineage.relations) != manifest["lineage_relation_count"]:
                raise IntegrityViolationError(
                    "evidence bundle lineage relation count does not match"
                )
        elif manifest.get("lineage_node_count") != 0 or manifest.get("lineage_relation_count") != 0:
            raise IntegrityViolationError("evidence bundle declares lineage without lineage evidence")

        return cast(dict[str, object], manifest)
