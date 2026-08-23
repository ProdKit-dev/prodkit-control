from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
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
_MANIFEST_SCHEMA_NAME = "prodkit.evidence-bundle-manifest"
_MANIFEST_SCHEMA_VERSION = "1.1.0"


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
        tenant_id: str,
        lineage: LineageGraph | None = None,
    ) -> Path:
        await self._ledger.verify_run(tenant_id=tenant_id, run_id=run_id)
        events = await self._ledger.list_run_events(tenant_id=tenant_id, run_id=run_id)
        if not events:
            raise ValueError(f"run {run_id} has no events for tenant {tenant_id!r}")
        if any(event.tenant_id != tenant_id for event in events):
            raise IntegrityViolationError("evidence builder received a foreign-tenant event")
        projection = project_run(events)
        destination.mkdir(parents=True, exist_ok=True)
        events_path = destination / "events.jsonl"
        events_bytes = b"\n".join(canonical_json_bytes(event) for event in events) + b"\n"
        events_path.write_bytes(events_bytes)
        if lineage is not None and (
            lineage.run_id != run_id or lineage.tenant_id != tenant_id
        ):
            raise ValueError("lineage graph and evidence bundle scope must match")
        files = {"events.jsonl": sha256_hex(events_bytes)}
        lineage_bytes = canonical_json_bytes(lineage) if lineage is not None else None
        if lineage_bytes is not None:
            files["lineage.json"] = sha256_hex(lineage_bytes)
        manifest = {
            "schema_name": _MANIFEST_SCHEMA_NAME,
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "run_id": str(run_id),
            "tenant_id": tenant_id,
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

        manifest_bytes, events_bytes, lineage_bytes = self._read_archive(archive)
        manifest = self._load_manifest(manifest_bytes)
        run_id, manifest_tenant_id = self._validate_manifest(
            manifest, events_bytes, lineage_bytes
        )
        event_tenant_id = self._validate_events(manifest, run_id, events_bytes)
        if event_tenant_id != manifest_tenant_id:
            raise IntegrityViolationError("evidence bundle tenant does not match events")
        self._validate_lineage(manifest, run_id, event_tenant_id, lineage_bytes)
        return cast(dict[str, object], manifest)

    @staticmethod
    def _read_archive(archive: Path) -> tuple[bytes, bytes, bytes | None]:
        try:
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
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise IntegrityViolationError("evidence bundle is not a valid archive") from exc
        return manifest_bytes, events_bytes, lineage_bytes

    @staticmethod
    def _load_manifest(payload: bytes) -> dict[str, Any]:
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrityViolationError("evidence bundle manifest is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise IntegrityViolationError("evidence bundle manifest must be an object")
        return cast(dict[str, Any], raw)

    @staticmethod
    def _validate_manifest(
        manifest: dict[str, Any],
        events_bytes: bytes,
        lineage_bytes: bytes | None,
    ) -> tuple[str, str]:
        if manifest.get("schema_name") != _MANIFEST_SCHEMA_NAME:
            raise IntegrityViolationError("evidence bundle manifest schema name is unsupported")
        if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            raise IntegrityViolationError("evidence bundle manifest schema version is unsupported")

        run_id_raw = manifest.get("run_id")
        if not isinstance(run_id_raw, str):
            raise IntegrityViolationError("evidence bundle manifest run id is invalid")
        try:
            run_id = str(UUID(run_id_raw))
        except ValueError as exc:
            raise IntegrityViolationError("evidence bundle manifest run id is invalid") from exc
        if run_id != run_id_raw:
            raise IntegrityViolationError("evidence bundle manifest run id is not canonical")
        tenant_id = manifest.get("tenant_id")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise IntegrityViolationError("evidence bundle manifest tenant is invalid")

        event_count = _manifest_count(manifest, "event_count", minimum=1)
        _manifest_count(manifest, "lineage_node_count", minimum=0)
        _manifest_count(manifest, "lineage_relation_count", minimum=0)

        files = manifest.get("files")
        if not isinstance(files, dict):
            raise IntegrityViolationError("evidence bundle manifest files are invalid")
        expected_members = {"events.jsonl"}
        if lineage_bytes is not None:
            expected_members.add("lineage.json")
        if set(files) != expected_members:
            raise IntegrityViolationError("evidence bundle manifest members do not match archive")

        events_digest = files.get("events.jsonl")
        if not _is_sha256(events_digest):
            raise IntegrityViolationError("evidence bundle events digest is invalid")
        actual_events_digest = sha256_hex(events_bytes)
        if actual_events_digest != events_digest or actual_events_digest != manifest.get(
            "events_sha256"
        ):
            raise IntegrityViolationError("evidence bundle events digest does not match manifest")

        final_event_hash = manifest.get("final_event_hash")
        if not _is_sha256(final_event_hash):
            raise IntegrityViolationError("evidence bundle final event hash is invalid")
        counts = manifest.get("counts_by_type")
        if not isinstance(counts, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in counts.items()
        ):
            raise IntegrityViolationError("evidence bundle event counts are invalid")
        if sum(cast(dict[str, int], counts).values()) != event_count:
            raise IntegrityViolationError("evidence bundle event counts do not match event count")
        return run_id, tenant_id

    @staticmethod
    def _validate_events(
        manifest: dict[str, Any],
        run_id: str,
        events_bytes: bytes,
    ) -> str:
        lines = events_bytes.splitlines()
        if not lines or any(not line for line in lines):
            raise IntegrityViolationError("evidence bundle contains an invalid event stream")
        if len(lines) != manifest["event_count"]:
            raise IntegrityViolationError("evidence bundle event count does not match manifest")

        previous: str | None = None
        tenant_id: str | None = None
        counts: Counter[str] = Counter()
        for index, line in enumerate(lines, start=1):
            try:
                raw_event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise IntegrityViolationError(
                    "evidence bundle contains malformed event JSON"
                ) from exc
            if not isinstance(raw_event, dict):
                raise IntegrityViolationError("evidence bundle event must be an object")
            event = cast(dict[str, Any], raw_event)
            if event.get("sequence") != index:
                raise IntegrityViolationError("evidence bundle contains a sequence gap")
            if event.get("run_id") != run_id:
                raise IntegrityViolationError(
                    "evidence bundle event run id does not match manifest"
                )
            event_tenant = event.get("tenant_id")
            if not isinstance(event_tenant, str) or not event_tenant:
                raise IntegrityViolationError("evidence bundle event tenant is invalid")
            if tenant_id is None:
                tenant_id = event_tenant
            elif tenant_id != event_tenant:
                raise IntegrityViolationError("evidence bundle contains mixed tenant events")
            event_type = event.get("event_type")
            if not isinstance(event_type, str) or not event_type:
                raise IntegrityViolationError("evidence bundle event type is invalid")
            counts[event_type] += 1

            integrity_raw = event.pop("integrity", None)
            if not isinstance(integrity_raw, dict):
                raise IntegrityViolationError("evidence bundle event integrity is invalid")
            integrity = cast(dict[str, Any], integrity_raw)
            if integrity.get("previous_event_hash") != previous:
                raise IntegrityViolationError("evidence bundle contains a broken previous hash")
            event_hash = integrity.get("event_hash")
            if not _is_sha256(event_hash):
                raise IntegrityViolationError("evidence bundle event hash is invalid")
            expected_hash = sha256_hex({"event": event, "previous_event_hash": previous})
            if event_hash != expected_hash:
                raise IntegrityViolationError("evidence bundle contains a modified event")
            previous = event_hash

        if previous != manifest["final_event_hash"]:
            raise IntegrityViolationError("evidence bundle final hash does not match manifest")
        if dict(counts) != manifest["counts_by_type"]:
            raise IntegrityViolationError("evidence bundle event-type counts do not match manifest")
        if tenant_id is None:  # pragma: no cover - non-empty event stream is enforced above
            raise IntegrityViolationError("evidence bundle contains no tenant evidence")
        return tenant_id

    @staticmethod
    def _validate_lineage(
        manifest: dict[str, Any],
        run_id: str,
        event_tenant_id: str,
        lineage_bytes: bytes | None,
    ) -> None:
        files = cast(dict[str, Any], manifest["files"])
        if lineage_bytes is None:
            if manifest["lineage_node_count"] != 0 or manifest["lineage_relation_count"] != 0:
                raise IntegrityViolationError(
                    "evidence bundle declares lineage without lineage evidence"
                )
            return

        lineage_digest = files.get("lineage.json")
        if not _is_sha256(lineage_digest) or sha256_hex(lineage_bytes) != lineage_digest:
            raise IntegrityViolationError("evidence bundle lineage digest does not match manifest")
        try:
            lineage = LineageGraph.model_validate_json(lineage_bytes)
        except ValueError as exc:
            raise IntegrityViolationError("evidence bundle lineage is invalid") from exc
        if str(lineage.run_id) != run_id:
            raise IntegrityViolationError("evidence bundle lineage run id does not match manifest")
        if lineage.tenant_id != event_tenant_id:
            raise IntegrityViolationError("evidence bundle lineage tenant does not match events")
        if len(lineage.nodes) != manifest["lineage_node_count"]:
            raise IntegrityViolationError("evidence bundle lineage node count does not match")
        if len(lineage.relations) != manifest["lineage_relation_count"]:
            raise IntegrityViolationError("evidence bundle lineage relation count does not match")


def _manifest_count(manifest: dict[str, Any], key: str, *, minimum: int) -> int:
    value = manifest.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise IntegrityViolationError(f"evidence bundle manifest {key} is invalid")
    return value


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)
