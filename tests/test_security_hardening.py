from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prodkit_control_core import (
    ArtifactProvenancePolicy,
    AttestationResourceDescriptor,
    InTotoStatementV1,
    OperationalSLO,
    RateLimitPolicy,
    SecretReference,
    SecurityAuditEvent,
    SecurityOutcome,
    SecuritySeverity,
    WorkloadIdentityAssertion,
    WorkloadIdentityPolicy,
)
from prodkit_control_runtime.security import (
    ArtifactProvenanceVerifier,
    InMemoryReplayStore,
    NDJSONSecurityAuditExporter,
    SecretReferenceGuard,
    SlidingWindowRateLimiter,
    WorkloadIdentityVerifier,
    evaluate_slo,
    redact_security_attributes,
)
from scripts import check_security_policy


class _StaticWorkloadAuthenticator:
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
        if credential != "signed-workload-token":
            raise PermissionError("workload identity credential is not authenticated")
        return self._assertion


class _RejectingWorkloadAuthenticator:
    def authenticate(
        self,
        credential: str,
        *,
        expected_issuer: str,
        expected_audience: str,
        now: datetime,
    ) -> WorkloadIdentityAssertion:
        del credential, expected_issuer, expected_audience, now
        raise PermissionError("workload identity credential is not authenticated")


class _NeverClaimReplayStore:
    def claim_once(self, *, key: str, expires_at: datetime, now: datetime) -> bool:
        del key, expires_at, now
        raise AssertionError("replay state must not be touched before authentication")


class _NoItemsDict(dict[str, object]):
    def items(self):  # type: ignore[override]
        raise AssertionError("rate-limit checks must not scan every bucket")


def test_secret_reference_guard_requires_approved_versioned_binding() -> None:
    guard = SecretReferenceGuard(allowed_providers=("vault", "aws-secrets-manager"))
    reference = SecretReference(
        provider="vault",
        reference="kv/prod/control/signing-key",
        version="42",
        tenant_id="tenant-a",
        purpose="signing",
        audience=("executor-a",),
    )
    guard.validate(reference, tenant_id="tenant-a", purpose="signing", audience="executor-a")

    with pytest.raises(PermissionError, match="version-pinned"):
        guard.validate(
            reference.model_copy(update={"version": None}),
            tenant_id="tenant-a",
            purpose="signing",
        )
    with pytest.raises(PermissionError, match="binding mismatch"):
        guard.validate(reference, tenant_id="tenant-b", purpose="signing")
    with pytest.raises(PermissionError, match="audience mismatch"):
        guard.validate(reference, tenant_id="tenant-a", purpose="signing", audience="executor-b")
    with pytest.raises(PermissionError, match="audience mismatch"):
        guard.validate(
            reference.model_copy(update={"audience": ()}),
            tenant_id="tenant-a",
            purpose="signing",
            audience="executor-a",
        )


def test_secret_reference_contract_rejects_inline_material() -> None:
    for inline_reference in (
        "password=hunter2",
        "api_key=live-api-key",
        "api-key=live-api-key",
        "apikey=live-api-key",
        "access_key=live-access-key",
        "access-key=live-access-key",
        "client_secret=live-client-secret",
        "postgresql://user:hunter2@host/db",
    ):
        with pytest.raises(ValueError, match="inline secret material"):
            SecretReference(
                provider="vault",
                reference=inline_reference,
                version="1",
                tenant_id="tenant-a",
                purpose="database",
            )


def _workload_assertion(now: datetime, *, nonce: str = "nonce-1") -> WorkloadIdentityAssertion:
    return WorkloadIdentityAssertion(
        issuer="https://issuer.example",
        subject="spiffe://prodkit/executor/a",
        audience="prodkit-control-exchange",
        tenant_id="tenant-a",
        client_id="executor",
        issued_at=now - timedelta(seconds=5),
        not_before=now - timedelta(seconds=5),
        expires_at=now + timedelta(seconds=55),
        nonce=nonce,
    )


def _workload_policy() -> WorkloadIdentityPolicy:
    return WorkloadIdentityPolicy(
        issuer="https://issuer.example",
        audience="prodkit-control-exchange",
        subject_prefixes=("spiffe://prodkit/executor/",),
        allowed_client_ids=("executor",),
        max_assertion_lifetime_seconds=120,
        clock_skew_seconds=0,
    )


def _workload_verifier(assertion: WorkloadIdentityAssertion) -> WorkloadIdentityVerifier:
    return WorkloadIdentityVerifier(
        _workload_policy(),
        InMemoryReplayStore(max_entries=100),
        authenticator=_StaticWorkloadAuthenticator(assertion),
    )


def test_workload_identity_authenticates_before_replay_claim() -> None:
    verifier = WorkloadIdentityVerifier(
        _workload_policy(),
        _NeverClaimReplayStore(),
        authenticator=_RejectingWorkloadAuthenticator(),
    )
    with pytest.raises(PermissionError, match="not authenticated"):
        verifier.verify_and_claim(
            "forged-credential",
            tenant_id="tenant-a",
            now=datetime.now(UTC),
        )


def test_workload_identity_rejects_replay_and_wrong_bindings() -> None:
    now = datetime.now(UTC)
    assertion = _workload_assertion(now)
    verifier = _workload_verifier(assertion)
    verifier.verify_and_claim("signed-workload-token", tenant_id="tenant-a", now=now)

    with pytest.raises(PermissionError, match="replay"):
        verifier.verify_and_claim("signed-workload-token", tenant_id="tenant-a", now=now)
    with pytest.raises(PermissionError, match="tenant mismatch"):
        _workload_verifier(assertion).verify_and_claim(
            "signed-workload-token", tenant_id="tenant-b", now=now
        )
    with pytest.raises(PermissionError, match="audience mismatch"):
        _workload_verifier(assertion.model_copy(update={"audience": "wrong"})).verify_and_claim(
            "signed-workload-token", tenant_id="tenant-a", now=now
        )
    with pytest.raises(PermissionError, match="subject"):
        _workload_verifier(
            assertion.model_copy(update={"subject": "spiffe://attacker/workload"})
        ).verify_and_claim("signed-workload-token", tenant_id="tenant-a", now=now)
    with pytest.raises(PermissionError, match="client"):
        _workload_verifier(assertion.model_copy(update={"client_id": "unknown"})).verify_and_claim(
            "signed-workload-token", tenant_id="tenant-a", now=now
        )


def test_workload_replay_claim_is_atomic_under_race() -> None:
    now = datetime.now(UTC)
    assertion = _workload_assertion(now, nonce="same-nonce")
    verifier = _workload_verifier(assertion)

    def attempt() -> bool:
        try:
            verifier.verify_and_claim("signed-workload-token", tenant_id="tenant-a", now=now)
        except PermissionError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(64)))
    assert outcomes.count(True) == 1


def test_workload_identity_validity_window_fails_closed() -> None:
    now = datetime.now(UTC)
    assertion = _workload_assertion(now).model_copy(
        update={"expires_at": now - timedelta(seconds=1)}
    )
    verifier = _workload_verifier(assertion)
    with pytest.raises(PermissionError, match="validity window"):
        verifier.verify_and_claim("signed-workload-token", tenant_id="tenant-a", now=now)


def test_sliding_window_rate_limiter_caps_concurrent_burst() -> None:
    current = [100.0]
    limiter = SlidingWindowRateLimiter(
        RateLimitPolicy(policy_id="api", limit=3, burst=1, window_seconds=10, max_keys=10),
        clock=lambda: current[0],
    )
    decisions = [limiter.check("tenant-a:principal-a") for _ in range(5)]
    assert [item.allowed for item in decisions] == [True, True, True, True, False]
    assert decisions[-1].retry_after_seconds == 10

    current[0] = 111.0
    assert limiter.check("tenant-a:principal-a").allowed is True


def test_sliding_window_rate_limiter_fails_closed_at_key_capacity() -> None:
    current = [100.0]
    limiter = SlidingWindowRateLimiter(
        RateLimitPolicy(policy_id="api", limit=3, burst=0, window_seconds=10, max_keys=1),
        clock=lambda: current[0],
    )

    assert limiter.check("tenant-a:principal-a").allowed is True
    denied = limiter.check("tenant-a:principal-b")
    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after_seconds == 10
    assert limiter.check("tenant-a:principal-c").allowed is False
    assert limiter.check("tenant-a:principal-a").allowed is True

    current[0] = 111.0
    assert limiter.check("tenant-a:principal-b").allowed is True


def test_sliding_window_rate_limiter_does_not_scan_all_buckets() -> None:
    current = [100.0]
    limiter = SlidingWindowRateLimiter(
        RateLimitPolicy(policy_id="api", limit=3, window_seconds=10, max_keys=10),
        clock=lambda: current[0],
    )
    assert limiter.check("tenant-a:principal-a").allowed is True
    assert limiter.check("tenant-b:principal-b").allowed is True
    limiter._entries = _NoItemsDict(limiter._entries)  # type: ignore[assignment]
    assert limiter.check("tenant-a:principal-a").allowed is True


def test_workflow_pin_policy_scans_yaml_step_uses(tmp_path, monkeypatch) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "pinned.yml").write_text(
        f"jobs:\n  check:\n    uses: actions/checkout@{'a' * 40}\n",
        encoding="utf-8",
    )
    (workflows / "unpinned.yaml").write_text(
        "jobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_security_policy, "WORKFLOWS", workflows)
    monkeypatch.setattr(check_security_policy, "ROOT", tmp_path)

    with pytest.raises(SystemExit, match=r"unpinned\.yaml"):
        check_security_policy.check_workflow_pins()


def test_workflow_pin_policy_scans_nested_composite_actions(tmp_path, monkeypatch) -> None:
    workflows = tmp_path / ".github" / "workflows"
    local_action = tmp_path / ".github" / "actions" / "local"
    workflows.mkdir(parents=True)
    local_action.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/local\n",
        encoding="utf-8",
    )
    (local_action / "action.yml").write_text(
        "name: local\nruns:\n  using: composite\n  steps:\n    - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_security_policy, "WORKFLOWS", workflows)
    monkeypatch.setattr(check_security_policy, "ROOT", tmp_path)

    with pytest.raises(SystemExit, match=r"action\.yml"):
        check_security_policy.check_workflow_pins()


def test_security_audit_export_redacts_credential_like_fields() -> None:
    lines: list[str] = []
    event = SecurityAuditEvent(
        event_id=uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="workload_identity.replay",
        severity=SecuritySeverity.HIGH,
        outcome=SecurityOutcome.DENIED,
        tenant_id="tenant-a",
        attributes={
            "authorization": "Bearer should-not-leak",
            "api_key": "live-api-key",
            "access-key-id": "live-access-key",
            "route": "/v1/actions",
        },
    )
    NDJSONSecurityAuditExporter(lines.append).export((event,))
    exported = json.loads(lines[0])
    assert exported["attributes"]["authorization"] == "[REDACTED]"
    assert exported["attributes"]["api_key"] == "[REDACTED]"
    assert exported["attributes"]["access-key-id"] == "[REDACTED]"
    assert exported["attributes"]["route"] == "/v1/actions"
    assert "should-not-leak" not in lines[0]
    assert "live-api-key" not in lines[0]
    assert "live-access-key" not in lines[0]
    assert redact_security_attributes({"session_cookie": "abc", "apikey": "def"}) == {
        "session_cookie": "[REDACTED]",
        "apikey": "[REDACTED]",
    }


def _provenance_statement(*, digest: str = "a" * 64) -> InTotoStatementV1:
    return InTotoStatementV1.model_validate(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "artifact.whl", "digest": {"sha256": digest}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "buildType": "https://github.com/Attestations/GitHubActionsWorkflow@v1",
                    "externalParameters": {},
                    "internalParameters": {},
                    "resolvedDependencies": [],
                },
                "runDetails": {
                    "builder": {"id": "https://github.com/ProdKit-dev/prodkit-workflows"},
                    "metadata": {},
                    "byproducts": [],
                },
            },
        }
    )


def test_provenance_policy_requires_digest_builder_and_verified_signature() -> None:
    policy = ArtifactProvenancePolicy(
        policy_id="release",
        allowed_builder_ids=("https://github.com/ProdKit-dev/prodkit-workflows",),
        allowed_build_types=("https://github.com/Attestations/GitHubActionsWorkflow@v1",),
    )
    verifier = ArtifactProvenanceVerifier(policy)
    statement = _provenance_statement()
    verifier.verify(statement, artifact_sha256="a" * 64, signature_verified=True)

    with pytest.raises(PermissionError, match="signature"):
        verifier.verify(statement, artifact_sha256="a" * 64, signature_verified=False)
    with pytest.raises(PermissionError, match="digest"):
        verifier.verify(statement, artifact_sha256="b" * 64, signature_verified=True)
    with pytest.raises(PermissionError, match="builder"):
        attacker = _provenance_statement().model_copy(
            update={
                "predicate": {
                    **_provenance_statement().predicate,
                    "runDetails": {
                        "builder": {"id": "https://attacker.example/builder"},
                        "metadata": {},
                        "byproducts": [],
                    },
                }
            }
        )
        verifier.verify(attacker, artifact_sha256="a" * 64, signature_verified=True)


def test_slo_evaluation_pages_on_fast_budget_burn() -> None:
    slo = OperationalSLO(
        slo_id="api-availability",
        metric="successful_control_api_requests",
        target_ratio=0.999,
        window_seconds=2_592_000,
        page_burn_rate=14.4,
        ticket_burn_rate=6.0,
    )
    healthy = evaluate_slo(slo, good=999, total=1000)
    assert healthy.page is False
    assert healthy.ticket is False

    degraded = evaluate_slo(slo, good=980, total=1000)
    assert degraded.burn_rate > 14.4
    assert degraded.page is True
    assert degraded.ticket is True


def test_attestation_subject_requires_digest() -> None:
    with pytest.raises(ValueError, match="immutable digest"):
        AttestationResourceDescriptor(name="artifact.whl")
