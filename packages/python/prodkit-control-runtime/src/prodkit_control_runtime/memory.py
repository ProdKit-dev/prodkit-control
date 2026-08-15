from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from prodkit_control_core import (
    ActionSpec,
    ApprovalDecision,
    ArtifactRef,
    ContentStorageMode,
    DuplicateActionError,
    EventIntegrity,
    ControlEvent,
    ControlEventDraft,
    ExecutionResult,
    IntegrityViolationError,
    PolicyDecision,
    canonical_json_bytes,
    sha256_hex,
)


class InMemoryEventLedger:
    """Deterministic append-only ledger with one lock and hash chain per run."""

    def __init__(self) -> None:
        self._events: dict[UUID, list[ControlEvent]] = defaultdict(list)
        self._locks: dict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def append(self, draft: ControlEventDraft) -> ControlEvent:
        async with self._locks[draft.run_id]:
            run_events = self._events[draft.run_id]
            sequence = len(run_events) + 1
            previous = run_events[-1].integrity.event_hash if run_events else None
            material = {**draft.model_dump(mode="python"), "sequence": sequence}
            event_hash = sha256_hex({"event": material, "previous_event_hash": previous})
            event = ControlEvent(
                **draft.model_dump(mode="python"),
                sequence=sequence,
                integrity=EventIntegrity(
                    previous_event_hash=previous,
                    event_hash=event_hash,
                ),
            )
            run_events.append(event)
            return event

    async def list_run_events(self, run_id: UUID) -> list[ControlEvent]:
        return list(self._events.get(run_id, ()))

    async def stream_run_events(self, run_id: UUID) -> AsyncIterator[ControlEvent]:
        for event in await self.list_run_events(run_id):
            yield event

    async def verify_run(self, run_id: UUID) -> None:
        previous: str | None = None
        expected_sequence = 1
        for event in await self.list_run_events(run_id):
            if event.sequence != expected_sequence:
                raise IntegrityViolationError(
                    f"run {run_id} expected sequence {expected_sequence}, got {event.sequence}"
                )
            if event.integrity.previous_event_hash != previous:
                raise IntegrityViolationError(
                    f"run {run_id} sequence {event.sequence} has invalid previous hash"
                )
            expected = sha256_hex(
                {
                    "event": event.hash_material(),
                    "previous_event_hash": previous,
                }
            )
            if event.integrity.event_hash != expected:
                raise IntegrityViolationError(
                    f"run {run_id} sequence {event.sequence} has invalid event hash"
                )
            previous = event.integrity.event_hash
            expected_sequence += 1

    def replace_for_test(self, run_id: UUID, events: list[ControlEvent]) -> None:
        """Testing hook used to prove tamper detection."""

        self._events[run_id] = list(events)


class InMemoryArtifactStore:
    """Content-addressed artifact store suitable for tests and local development."""

    def __init__(self, root: Path | None = None) -> None:
        self._content: dict[str, bytes] = {}
        self._root = root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)

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
        artifact_id = f"artifact-{digest}"
        location = f"memory://{tenant_id}/{digest}"
        self._content[location] = stored
        if self._root is not None:
            path = self._root / tenant_id / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(stored)
            location = path.resolve().as_uri()
        return ArtifactRef(
            artifact_id=artifact_id,
            media_type=media_type,
            sha256=digest,
            size_bytes=len(stored),
            storage_mode=ContentStorageMode.REDACTED if redact else ContentStorageMode.FULL,
            location=location,
            encrypted=False,
            redacted=redact,
            redaction_version="runtime-redaction-v1" if redact else None,
            classification=classification,
        )

    async def get(self, artifact: ArtifactRef) -> bytes:
        if artifact.location is None:
            raise KeyError("artifact has no retrievable location")
        if artifact.location.startswith("file:"):
            path = Path(artifact.location.removeprefix("file://"))
            content = path.read_bytes()
        else:
            content = self._content[artifact.location]
        if sha256_hex(content) != artifact.sha256:
            raise IntegrityViolationError(
                f"artifact {artifact.artifact_id} failed digest verification"
            )
        return content


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._approvals: dict[UUID, list[ApprovalDecision]] = defaultdict(list)

    async def record(self, decision: ApprovalDecision) -> None:
        self._approvals[decision.action_id].append(decision)

    async def find_valid_approval(
        self,
        *,
        action: ActionSpec,
        policy_decision: PolicyDecision,
        target_digest: str,
    ) -> ApprovalDecision | None:
        now = datetime.now(UTC)
        for approval in reversed(self._approvals.get(action.action_id, ())):
            if approval.authorizes(
                action_digest=action.digest,
                target_digest=target_digest,
                policy_decision_id=policy_decision.decision_id,
                policy_revision=policy_decision.policy_revision,
                tenant_id=action.tenant_id,
                environment=action.target.environment,
                at=now,
            ):
                return approval
        return None


@dataclass
class _IdempotencyEntry:
    action_digest: str
    result: ExecutionResult | None = None


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _IdempotencyEntry] = {}
        self._lock = asyncio.Lock()

    async def claim(self, *, tenant_id: str, key: str, action_digest: str) -> bool:
        async with self._lock:
            identity = (tenant_id, key)
            current = self._entries.get(identity)
            if current is None:
                self._entries[identity] = _IdempotencyEntry(action_digest=action_digest)
                return True
            if current.action_digest != action_digest:
                raise DuplicateActionError(
                    f"idempotency key {key!r} already belongs to another action digest"
                )
            return False

    async def complete(self, *, tenant_id: str, key: str, result: ExecutionResult) -> None:
        async with self._lock:
            identity = (tenant_id, key)
            entry = self._entries.get(identity)
            if entry is None:
                raise KeyError(f"idempotency key {key!r} was not claimed")
            entry.result = result

    async def result(self, *, tenant_id: str, key: str) -> ExecutionResult | None:
        entry = self._entries.get((tenant_id, key))
        return entry.result if entry is not None else None
