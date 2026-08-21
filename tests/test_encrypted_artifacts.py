from __future__ import annotations

import json

import pytest

from prodkit_control_core import ContentStorageMode, IntegrityViolationError
from prodkit_control_runtime import EncryptedFilesystemArtifactStore


@pytest.mark.asyncio
async def test_encrypted_artifact_round_trip_and_redaction(tmp_path) -> None:
    store = EncryptedFilesystemArtifactStore(
        root=tmp_path,
        key=b"k" * 32,
        retention_days=7,
    )

    artifact = await store.put(
        tenant_id="tenant-a",
        media_type="text/plain",
        content=b"release evidence",
        classification="confidential",
    )
    assert artifact.encrypted is True
    assert artifact.storage_mode is ContentStorageMode.FULL
    assert artifact.classification == "confidential"
    assert await store.get(artifact) == b"release evidence"

    redacted = await store.put(
        tenant_id="tenant-a",
        media_type="application/json",
        content=b'{"secret":"value"}',
        redact=True,
    )
    assert redacted.storage_mode is ContentStorageMode.REDACTED
    envelope = json.loads((await store.get(redacted)).decode())
    assert envelope["redacted"] is True
    assert envelope["redaction_version"] == "runtime-redaction-v1"
    assert envelope["original_sha256"]


@pytest.mark.asyncio
async def test_encrypted_artifact_detects_ciphertext_tampering(tmp_path) -> None:
    store = EncryptedFilesystemArtifactStore(root=tmp_path, key=b"z" * 32)
    artifact = await store.put(
        tenant_id="tenant-a",
        media_type="application/octet-stream",
        content=b"immutable evidence",
    )
    assert artifact.location is not None
    relative = artifact.location.removeprefix("pkc+file://")
    path = tmp_path / relative
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)

    with pytest.raises(IntegrityViolationError, match="authentication/decryption failed"):
        await store.get(artifact)


def test_encrypted_artifact_store_validates_key_and_retention(tmp_path) -> None:
    with pytest.raises(ValueError, match="32-byte AES-256 key"):
        EncryptedFilesystemArtifactStore(root=tmp_path, key=b"short")
    with pytest.raises(ValueError, match="retention_days must be positive"):
        EncryptedFilesystemArtifactStore(
            root=tmp_path,
            key=b"k" * 32,
            retention_days=0,
        )
