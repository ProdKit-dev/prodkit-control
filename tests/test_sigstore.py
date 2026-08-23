from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from prodkit_sigstore import CosignClient, SigstoreIntegrationError


class _FakeCosign:
    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del timeout, env
        command = tuple(argv)
        self.calls.append(command)
        if self.returncode == 0 and "sign-blob" in command and "--bundle" in command:
            bundle = Path(command[command.index("--bundle") + 1])
            bundle.write_text('{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}')
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=self.returncode,
            stdout="verified" if self.returncode == 0 else "",
            stderr="failure" if self.returncode else "",
        )


def test_cosign_blob_bundle_roundtrip_uses_strict_argv(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"portable-evidence")
    bundle = tmp_path / "artifact.sigstore.json"
    fake = _FakeCosign()
    client = CosignClient(runner=fake)

    client.sign_blob(artifact, bundle=bundle, key="kms://release-key")
    client.verify_blob(artifact, bundle=bundle, key="kms://release-key", offline=True)

    assert fake.calls[0] == (
        "cosign",
        "sign-blob",
        str(artifact),
        "--bundle",
        str(bundle),
        "--key",
        "kms://release-key",
        "--yes",
    )
    assert fake.calls[1][-1] == "--offline"
    assert "--key" in fake.calls[1]


def test_cosign_keyless_verification_requires_identity_and_issuer(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"evidence")
    bundle = tmp_path / "bundle.sigstore.json"
    bundle.write_text("{}")
    client = CosignClient(runner=_FakeCosign())

    with pytest.raises(ValueError, match="requires both certificate identity and OIDC issuer"):
        client.verify_blob(artifact, bundle=bundle)

    result = client.verify_blob(
        artifact,
        bundle=bundle,
        certificate_identity="release@example.test",
        certificate_oidc_issuer="https://issuer.example.test",
    )
    assert result.argv[-1] == "--offline"


def test_cosign_nonzero_exit_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"evidence")
    bundle = tmp_path / "bundle.sigstore.json"
    bundle.write_text("{}")
    client = CosignClient(runner=_FakeCosign(returncode=1))

    with pytest.raises(SigstoreIntegrationError, match="status 1"):
        client.verify_blob(artifact, bundle=bundle, key="cosign.pub")
