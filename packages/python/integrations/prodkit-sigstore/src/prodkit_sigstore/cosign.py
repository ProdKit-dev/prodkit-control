from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SigstoreIntegrationError(RuntimeError):
    """Raised when Sigstore signing or verification cannot satisfy its contract."""


@dataclass(frozen=True, slots=True)
class CosignCommandResult:
    argv: tuple[str, ...]
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


def _default_runner(
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(env),
    )


class CosignClient:
    """Strict argv-based adapter around Cosign v3 blob bundles.

    ProdKit never parses human CLI output to infer trust. Exit status is the authoritative
    verification result; caller-provided identity, issuer, key, and trusted-root constraints are
    passed to Cosign unchanged. Shell execution is intentionally unavailable.
    """

    def __init__(
        self,
        *,
        executable: str = "cosign",
        timeout_seconds: float = 180.0,
        environment: Mapping[str, str] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        if not executable.strip():
            raise ValueError("cosign executable cannot be blank")
        if timeout_seconds <= 0:
            raise ValueError("cosign timeout must be positive")
        self._executable = executable
        self._timeout = timeout_seconds
        self._environment = dict(environment or {})
        self._runner = runner or _default_runner

    def sign_blob(
        self,
        artifact: Path,
        *,
        bundle: Path,
        key: str | None = None,
        trusted_root: Path | None = None,
        yes: bool = True,
    ) -> CosignCommandResult:
        self._require_file(artifact, "artifact")
        bundle.parent.mkdir(parents=True, exist_ok=True)
        argv = [self._executable, "sign-blob", str(artifact), "--bundle", str(bundle)]
        if key is not None:
            if not key.strip():
                raise ValueError("Sigstore key reference cannot be blank")
            argv.extend(("--key", key))
        if yes:
            argv.append("--yes")
        result = self._run(argv, trusted_root=trusted_root)
        if not bundle.is_file():
            raise SigstoreIntegrationError(
                "cosign succeeded without producing the requested bundle"
            )
        return result

    def verify_blob(
        self,
        artifact: Path,
        *,
        bundle: Path,
        key: str | None = None,
        certificate_identity: str | None = None,
        certificate_oidc_issuer: str | None = None,
        trusted_root: Path | None = None,
        offline: bool = True,
    ) -> CosignCommandResult:
        self._require_file(artifact, "artifact")
        self._require_file(bundle, "Sigstore bundle")
        if key is None and not (certificate_identity and certificate_oidc_issuer):
            raise ValueError(
                "keyless Sigstore verification requires both certificate identity and OIDC issuer"
            )
        if (certificate_identity is None) != (certificate_oidc_issuer is None):
            raise ValueError("certificate identity and OIDC issuer must be supplied together")

        argv = [self._executable, "verify-blob", str(artifact), "--bundle", str(bundle)]
        if key is not None:
            if not key.strip():
                raise ValueError("Sigstore key reference cannot be blank")
            argv.extend(("--key", key))
        if certificate_identity is not None and certificate_oidc_issuer is not None:
            argv.extend(("--certificate-identity", certificate_identity))
            argv.extend(("--certificate-oidc-issuer", certificate_oidc_issuer))
        if trusted_root is not None:
            self._require_file(trusted_root, "Sigstore trusted root")
            argv.extend(("--trusted-root", str(trusted_root)))
        if offline:
            argv.append("--offline")
        return self._run(argv, trusted_root=None)

    def _run(
        self,
        argv: Sequence[str],
        *,
        trusted_root: Path | None,
    ) -> CosignCommandResult:
        env = os.environ.copy()
        env.update(self._environment)
        if trusted_root is not None:
            self._require_file(trusted_root, "Sigstore trusted root")
            env["SIGSTORE_ROOT_FILE"] = str(trusted_root)
        try:
            completed = self._runner(argv, timeout=self._timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SigstoreIntegrationError(
                "cosign invocation failed before verification completed"
            ) from exc
        result = CosignCommandResult(
            argv=tuple(argv),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
            raise SigstoreIntegrationError(
                f"cosign exited with status {completed.returncode}: {detail[:2000]}"
            )
        return result

    @staticmethod
    def _require_file(path: Path, label: str) -> None:
        if not path.is_file():
            raise ValueError(f"{label} must be an existing regular file: {path}")
