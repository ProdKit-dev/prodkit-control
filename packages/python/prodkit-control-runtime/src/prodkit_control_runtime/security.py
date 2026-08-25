from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from prodkit_control_core import (
    ArtifactProvenancePolicy,
    InTotoStatementV1,
    OperationalSLO,
    RateLimitPolicy,
    SecretReference,
    SecurityAuditEvent,
    WorkloadIdentityAssertion,
    WorkloadIdentityPolicy,
)
from prodkit_control_core.contracts.attestations import SlsaProvenancePredicateV1


class ReplayStore(Protocol):
    """Atomic replay-claim boundary for one-time workload assertions."""

    def claim_once(self, *, key: str, expires_at: datetime, now: datetime) -> bool: ...


class SecurityAuditExporter(Protocol):
    """Provider-neutral export boundary for sanitized security events."""

    def export(self, events: Sequence[SecurityAuditEvent]) -> None: ...


class InMemoryReplayStore:
    """Thread-safe standalone replay store; production clusters should inject a shared store."""

    def __init__(self, *, max_entries: int = 100_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._claims: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def claim_once(self, *, key: str, expires_at: datetime, now: datetime) -> bool:
        if expires_at.tzinfo is None or now.tzinfo is None:
            raise ValueError("replay timestamps must be timezone-aware")
        with self._lock:
            expired = [claim for claim, expiry in self._claims.items() if expiry <= now]
            for claim in expired:
                del self._claims[claim]
            if key in self._claims:
                return False
            if len(self._claims) >= self._max_entries:
                raise RuntimeError("replay store capacity exhausted; fail closed")
            self._claims[key] = expires_at
            return True


class SecretReferenceGuard:
    """Validates that control-plane inputs contain only approved, immutable secret references."""

    def __init__(
        self,
        *,
        allowed_providers: Sequence[str],
        require_version: bool = True,
    ) -> None:
        providers = frozenset(item.strip() for item in allowed_providers if item.strip())
        if not providers:
            raise ValueError("at least one secret provider must be allowed")
        self._allowed_providers = providers
        self._require_version = require_version

    def validate(
        self,
        reference: SecretReference,
        *,
        tenant_id: str,
        purpose: str,
        audience: str | None = None,
    ) -> None:
        if reference.provider not in self._allowed_providers:
            raise PermissionError("secret provider is not allowed")
        if self._require_version and reference.version is None:
            raise PermissionError("production secret references must be version-pinned")
        if reference.tenant_id != tenant_id or reference.purpose != purpose:
            raise PermissionError("secret reference binding mismatch")
        if audience is not None and (not reference.audience or audience not in reference.audience):
            raise PermissionError("secret reference audience mismatch")


class WorkloadIdentityVerifier:
    """Policy and replay verifier for short-lived one-time workload exchange assertions."""

    def __init__(self, policy: WorkloadIdentityPolicy, replay_store: ReplayStore) -> None:
        self._policy = policy
        self._replay_store = replay_store

    def verify_and_claim(
        self,
        assertion: WorkloadIdentityAssertion,
        *,
        tenant_id: str,
        now: datetime | None = None,
    ) -> None:
        checked_at = now or datetime.now(UTC)
        if checked_at.tzinfo is None:
            raise ValueError("verification time must be timezone-aware")
        if assertion.tenant_id != tenant_id:
            raise PermissionError("workload identity tenant mismatch")
        skew = timedelta(seconds=self._policy.clock_skew_seconds)
        if assertion.issuer.rstrip("/") != self._policy.issuer.rstrip("/"):
            raise PermissionError("workload identity issuer mismatch")
        if assertion.audience != self._policy.audience:
            raise PermissionError("workload identity audience mismatch")
        if not any(
            assertion.subject.startswith(prefix) for prefix in self._policy.subject_prefixes
        ):
            raise PermissionError("workload identity subject is not allowed")
        if (
            self._policy.allowed_client_ids
            and assertion.client_id not in self._policy.allowed_client_ids
        ):
            raise PermissionError("workload identity client is not allowed")
        if assertion.lifetime > timedelta(seconds=self._policy.max_assertion_lifetime_seconds):
            raise PermissionError("workload identity assertion lifetime is too long")
        if assertion.issued_at > checked_at + skew or assertion.expires_at <= checked_at - skew:
            raise PermissionError("workload identity assertion is outside its validity window")
        if self._policy.require_not_before and assertion.not_before is None:
            raise PermissionError("workload identity assertion requires not_before")
        if assertion.not_before is not None and assertion.not_before > checked_at + skew:
            raise PermissionError("workload identity assertion is not active yet")
        if self._policy.require_nonce and assertion.nonce is None:
            raise PermissionError("workload identity assertion requires nonce")
        if assertion.nonce is not None:
            replay_key = sha256(
                f"{assertion.issuer}\0{assertion.subject}\0{assertion.tenant_id}\0{assertion.nonce}".encode()
            ).hexdigest()
            if not self._replay_store.claim_once(
                key=replay_key,
                expires_at=assertion.expires_at + skew,
                now=checked_at,
            ):
                raise PermissionError("workload identity assertion replay detected")


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    """Bounded thread-safe local limiter; distributed deployments should inject gateway/shared limits."""

    def __init__(
        self, policy: RateLimitPolicy, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._entries: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> RateLimitDecision:
        if not key.strip():
            raise ValueError("rate-limit key must be non-empty")
        now = self._clock()
        cutoff = now - self._policy.window_seconds
        capacity = self._policy.limit + self._policy.burst
        with self._lock:
            expired_keys: list[str] = []
            for existing_key, existing_events in self._entries.items():
                while existing_events and existing_events[0] <= cutoff:
                    existing_events.popleft()
                if not existing_events:
                    expired_keys.append(existing_key)
            for expired_key in expired_keys:
                del self._entries[expired_key]

            if key not in self._entries and len(self._entries) >= self._policy.max_keys:
                retry = min(
                    max(
                        1,
                        int(
                            events[0]
                            + self._policy.window_seconds
                            - now
                            + 0.999
                        ),
                    )
                    for events in self._entries.values()
                )
                return RateLimitDecision(False, 0, retry)

            events = self._entries.setdefault(key, deque())
            if len(events) >= capacity:
                retry = max(1, int(events[0] + self._policy.window_seconds - now + 0.999))
                return RateLimitDecision(False, 0, retry)
            events.append(now)
            return RateLimitDecision(True, capacity - len(events), 0)


_SENSITIVE_FRAGMENTS = (
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "session",
    "token",
)


def redact_security_attributes(attributes: Mapping[str, object]) -> dict[str, str]:
    """Produces an export-safe attribute map without credential-like values."""

    sanitized: dict[str, str] = {}
    for key, value in attributes.items():
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS):
            sanitized[str(key)] = "[REDACTED]"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[str(key)] = str(value)
        else:
            sanitized[str(key)] = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sanitized


class NDJSONSecurityAuditExporter:
    """Deterministic append callback exporter suitable for files, streams, and external shippers."""

    def __init__(self, append: Callable[[str], None]) -> None:
        self._append = append

    def export(self, events: Sequence[SecurityAuditEvent]) -> None:
        for event in events:
            payload = event.model_dump(mode="json")
            payload["attributes"] = redact_security_attributes(event.attributes)
            self._append(json.dumps(payload, sort_keys=True, separators=(",", ":")))


class ArtifactProvenanceVerifier:
    """Enforces SLSA subject/build policy after the signature layer establishes authenticity."""

    def __init__(self, policy: ArtifactProvenancePolicy) -> None:
        self._policy = policy

    def verify(
        self,
        statement: InTotoStatementV1,
        *,
        artifact_sha256: str,
        signature_verified: bool,
    ) -> None:
        if self._policy.require_verified_signature and not signature_verified:
            raise PermissionError("artifact provenance signature is not verified")
        if statement.predicate_type != self._policy.required_predicate_type:
            raise PermissionError("artifact provenance predicate type is not allowed")
        if not any(
            subject.digest.get("sha256") == artifact_sha256 for subject in statement.subject
        ):
            raise PermissionError("artifact digest is not covered by provenance")
        predicate = SlsaProvenancePredicateV1.model_validate(statement.predicate)
        builder_id = predicate.run_details.builder.id
        if builder_id not in self._policy.allowed_builder_ids:
            raise PermissionError("artifact provenance builder is not trusted")
        build_type = predicate.build_definition.build_type
        if self._policy.allowed_build_types and build_type not in self._policy.allowed_build_types:
            raise PermissionError("artifact provenance build type is not allowed")


@dataclass(frozen=True)
class SLOEvaluation:
    error_budget_fraction: float
    burn_rate: float
    page: bool
    ticket: bool


def evaluate_slo(slo: OperationalSLO, *, good: int, total: int) -> SLOEvaluation:
    if total <= 0 or good < 0 or good > total:
        raise ValueError("SLO samples require 0 <= good <= total and total > 0")
    observed_bad = (total - good) / total
    budget = 1.0 - slo.target_ratio
    burn_rate = observed_bad / budget if budget > 0 else (float("inf") if observed_bad else 0.0)
    return SLOEvaluation(
        error_budget_fraction=budget,
        burn_rate=burn_rate,
        page=burn_rate >= slo.page_burn_rate,
        ticket=burn_rate >= slo.ticket_burn_rate,
    )
