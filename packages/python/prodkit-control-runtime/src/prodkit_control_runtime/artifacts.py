from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from prodkit_control_core import (
    ArtifactRef,
    AuthorizationDeniedError,
    ContentStorageMode,
    IntegrityViolationError,
    canonical_json_bytes,
    sha256_hex,
)


class EncryptedFilesystemArtifactStore:
    """Tenant-partitioned AES-256-GCM artifact store with tenant-bound AAD."""

    _MAGIC = b"PKCA1"
    _NONCE_BYTES = 12

    def __init__(self, *, root: Path, key: bytes, retention_days: int = 30) -> None:
        if len(key) != 32:
            raise ValueError("encrypted artifact store requires a 32-byte AES-256 key")
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._cipher = AESGCM(key)
        self._retention = timedelta(days=retention_days)

    async def put(
        self,
        *,
        tenant_id: str,
        media_type: str,
        content: bytes,
        classification: str = "internal",
        redact: bool = False,
    ) -> ArtifactRef:
        original_digest = sha256_hex(content)
        stored = (
            canonical_json_bytes(
                {
                    "redacted": True,
                    "original_sha256": original_digest,
                    "redaction_version": "runtime-redaction-v1",
                }
            )
            if redact
            else content
        )
        digest = sha256_hex(stored)
        tenant_partition = sha256_hex({"tenant_id": tenant_id})
        relative = Path(tenant_partition) / digest[:2] / f"{digest}.pkca"
        path = (self._root / relative).resolve()
        self._assert_under_root(path)
        aad = self._aad(
            tenant_id=tenant_id,
            digest=digest,
            media_type=media_type,
            classification=classification,
            redacted=redact,
        )
        nonce = secrets.token_bytes(self._NONCE_BYTES)
        ciphertext = self._cipher.encrypt(nonce, stored, aad)
        payload = self._MAGIC + nonce + ciphertext
        await asyncio.to_thread(self._atomic_write, path, payload)
        return ArtifactRef(
            tenant_id=tenant_id,
            artifact_id=f"artifact-{digest}",
            media_type=media_type,
            sha256=digest,
            size_bytes=len(stored),
            storage_mode=ContentStorageMode.REDACTED if redact else ContentStorageMode.FULL,
            location=f"pkc+file://{relative.as_posix()}",
            encrypted=True,
            redacted=redact,
            redaction_version="runtime-redaction-v1" if redact else None,
            retention_until=datetime.now(UTC) + self._retention,
            classification=classification,
        )

    async def get(self, *, tenant_id: str, artifact: ArtifactRef) -> bytes:
        if artifact.tenant_id != tenant_id:
            raise AuthorizationDeniedError("artifact is not owned by the requested tenant")
        if not artifact.encrypted:
            raise IntegrityViolationError(
                "encrypted store refuses an unencrypted artifact reference"
            )
        if artifact.location is None or not artifact.location.startswith("pkc+file://"):
            raise KeyError("artifact does not contain an encrypted filesystem location")
        relative = Path(artifact.location.removeprefix("pkc+file://"))
        if relative.is_absolute() or ".." in relative.parts:
            raise IntegrityViolationError("artifact location escapes the configured store")
        expected_partition = sha256_hex({"tenant_id": tenant_id})
        if not relative.parts or relative.parts[0] != expected_partition:
            raise AuthorizationDeniedError("artifact storage partition does not match tenant")
        path = (self._root / relative).resolve()
        self._assert_under_root(path)
        payload = await asyncio.to_thread(path.read_bytes)
        if not payload.startswith(self._MAGIC):
            raise IntegrityViolationError("artifact ciphertext header is invalid")
        nonce_start = len(self._MAGIC)
        nonce_end = nonce_start + self._NONCE_BYTES
        if len(payload) <= nonce_end:
            raise IntegrityViolationError("artifact ciphertext is truncated")
        nonce = payload[nonce_start:nonce_end]
        ciphertext = payload[nonce_end:]
        aad = self._aad(
            tenant_id=tenant_id,
            digest=artifact.sha256,
            media_type=artifact.media_type,
            classification=artifact.classification,
            redacted=artifact.redacted,
        )
        try:
            content = self._cipher.decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise IntegrityViolationError("artifact authentication/decryption failed") from exc
        if sha256_hex(content) != artifact.sha256:
            raise IntegrityViolationError(
                f"artifact {artifact.artifact_id} failed plaintext digest verification"
            )
        return content

    def _assert_under_root(self, path: Path) -> None:
        if path != self._root and self._root not in path.parents:
            raise IntegrityViolationError("artifact path escapes the configured store")

    @staticmethod
    def _aad(
        *, tenant_id: str, digest: str, media_type: str, classification: str, redacted: bool
    ) -> bytes:
        return canonical_json_bytes(
            {
                "tenant_id": tenant_id,
                "sha256": digest,
                "media_type": media_type,
                "classification": classification,
                "redacted": redacted,
            }
        )

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
