from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prodkit_control_core import (
    ArtifactProvenancePolicy,
    InTotoStatementV1,
    RateLimitPolicy,
    WorkloadIdentityAssertion,
    WorkloadIdentityPolicy,
)
from prodkit_control_runtime import (
    ArtifactProvenanceVerifier,
    InMemoryReplayStore,
    SlidingWindowRateLimiter,
    WorkloadIdentityVerifier,
)


class _ExerciseWorkloadAuthenticator:
    def __init__(self, assertion: WorkloadIdentityAssertion) -> None:
        self._assertion = assertion

    def authenticate(
        self,
        credential: str,
        *,
        expected_issuer: str,
        expected_audience: str,
        now: datetime,
    ) -> WorkloadIdentityAssertion:
        del expected_issuer, expected_audience, now
        if credential != "exercise-signed-token":
            raise PermissionError("exercise workload credential is not authenticated")
        return self._assertion


def exercise_identity_replay() -> None:
    now = datetime.now(UTC)
    assertion = WorkloadIdentityAssertion(
        issuer="https://issuer.example",
        subject="spiffe://prodkit/executor/exercise",
        audience="prodkit-control-exchange",
        tenant_id="exercise-tenant",
        issued_at=now - timedelta(seconds=1),
        not_before=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=59),
        nonce="incident-exercise-replay",
    )
    verifier = WorkloadIdentityVerifier(
        WorkloadIdentityPolicy(
            issuer="https://issuer.example",
            audience="prodkit-control-exchange",
            subject_prefixes=("spiffe://prodkit/executor/",),
            max_assertion_lifetime_seconds=120,
            clock_skew_seconds=0,
        ),
        InMemoryReplayStore(),
        authenticator=_ExerciseWorkloadAuthenticator(assertion),
    )
    verifier.verify_and_claim(
        "exercise-signed-token",
        tenant_id="exercise-tenant",
        now=now,
    )
    try:
        verifier.verify_and_claim(
            "exercise-signed-token",
            tenant_id="exercise-tenant",
            now=now,
        )
    except PermissionError:
        return
    raise RuntimeError("identity replay exercise failed to detect replay")


def exercise_abuse_limit() -> None:
    limiter = SlidingWindowRateLimiter(
        RateLimitPolicy(policy_id="exercise", limit=2, window_seconds=60, max_keys=10),
        clock=lambda: 1.0,
    )
    if not limiter.check("exercise").allowed or not limiter.check("exercise").allowed:
        raise RuntimeError("abuse exercise rejected requests before policy limit")
    if limiter.check("exercise").allowed:
        raise RuntimeError("abuse exercise failed to enforce request limit")


def exercise_provenance_rejection() -> None:
    statement = InTotoStatementV1.model_validate(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "artifact.whl", "digest": {"sha256": "a" * 64}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {"buildType": "exercise", "resolvedDependencies": []},
                "runDetails": {
                    "builder": {"id": "https://attacker.example/builder"},
                    "metadata": {},
                    "byproducts": [],
                },
            },
        }
    )
    verifier = ArtifactProvenanceVerifier(
        ArtifactProvenancePolicy(
            policy_id="exercise",
            allowed_builder_ids=("https://github.com/ProdKit-dev/prodkit-workflows",),
        )
    )
    try:
        verifier.verify(statement, artifact_sha256="a" * 64, signature_verified=True)
    except PermissionError:
        return
    raise RuntimeError("supply-chain exercise accepted an untrusted builder")


def main() -> None:
    exercise_identity_replay()
    exercise_abuse_limit()
    exercise_provenance_rejection()
    print("incident exercise: replay, abuse, and supply-chain controls blocked adversarial cases")


if __name__ == "__main__":
    main()
