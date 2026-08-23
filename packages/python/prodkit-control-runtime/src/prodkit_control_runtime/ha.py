from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from prodkit_control_core import (
    CapacityEnvelope,
    CapacityExceededError,
    DurableWorkItem,
    DurableWorkQueue,
    DuplicateActionError,
    FencedLease,
    LeaseLostError,
    LeasedWorkItem,
    QueueOverloadedError,
    QueueSnapshot,
    RuntimeDrainingError,
    WorkState,
)

Clock = Callable[[], datetime]
WorkHandler = Callable[[LeasedWorkItem], Awaitable[None]]


REFERENCE_CAPACITY_ENVELOPE = CapacityEnvelope(
    profile_id="reference-ha",
    max_queue_depth=1_000,
    max_in_flight=128,
    max_per_tenant_in_flight=32,
    lease_ttl_seconds=30.0,
    shutdown_grace_seconds=30.0,
    qualification_concurrency=128,
    qualification_work_items=1_000,
    qualification_soak_seconds=10.0,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_positive_ttl(ttl_seconds: float) -> None:
    if ttl_seconds <= 0 or ttl_seconds > 86400:
        raise ValueError("lease TTL must be > 0 and <= 86400 seconds")


@dataclass
class _LeaseSlot:
    fence_token: int = 0
    current: FencedLease | None = None


class InMemoryLeaseStore:
    """Standalone lease store with the same fencing semantics as durable deployments."""

    def __init__(self, *, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._slots: dict[tuple[str, str], _LeaseSlot] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> FencedLease | None:
        _require_positive_ttl(ttl_seconds)
        if not tenant_id.strip() or not resource_key.strip() or not owner_id.strip():
            raise ValueError("lease tenant, resource, and owner must be non-blank")
        async with self._lock:
            now = self._clock()
            identity = (tenant_id, resource_key)
            slot = self._slots.setdefault(identity, _LeaseSlot())
            current = slot.current
            if current is not None and not current.is_expired(now):
                return current if current.owner_id == owner_id else None
            slot.fence_token += 1
            lease = FencedLease(
                lease_id=uuid4(),
                tenant_id=tenant_id,
                resource_key=resource_key,
                owner_id=owner_id,
                fence_token=slot.fence_token,
                acquired_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            slot.current = lease
            return lease

    async def renew(self, lease: FencedLease, *, ttl_seconds: float) -> FencedLease:
        _require_positive_ttl(ttl_seconds)
        async with self._lock:
            now = self._clock()
            slot = self._slots.get((lease.tenant_id, lease.resource_key))
            current = slot.current if slot is not None else None
            if not self._matches(current, lease) or current is None or current.is_expired(now):
                raise LeaseLostError("cannot renew a stale, released, or expired lease")
            renewed = current.model_copy(
                update={"expires_at": now + timedelta(seconds=ttl_seconds)}
            )
            slot.current = renewed
            return renewed

    async def release(self, lease: FencedLease) -> None:
        async with self._lock:
            now = self._clock()
            slot = self._slots.get((lease.tenant_id, lease.resource_key))
            current = slot.current if slot is not None else None
            if not self._matches(current, lease) or current is None or current.is_expired(now):
                raise LeaseLostError("cannot release a stale, replaced, or expired lease")
            slot.current = None

    async def is_current(self, lease: FencedLease) -> bool:
        async with self._lock:
            slot = self._slots.get((lease.tenant_id, lease.resource_key))
            current = slot.current if slot is not None else None
            return (
                current is not None
                and self._matches(current, lease)
                and not current.is_expired(self._clock())
            )

    @staticmethod
    def _matches(current: FencedLease | None, candidate: FencedLease) -> bool:
        return (
            current is not None
            and current.lease_id == candidate.lease_id
            and current.owner_id == candidate.owner_id
            and current.fence_token == candidate.fence_token
        )


class InMemoryDurableWorkQueue:
    """Bounded recoverable queue for standalone deployments and deterministic qualification tests.

    The queue fences *scheduler ownership*. A handler that produces an externally visible effect must
    additionally propagate ``lease.fence_token`` to a fence-aware sink or use a provider-enforced
    idempotency key. Unknown external-effect outcomes must still follow ActionBroker reconciliation
    rules rather than being blindly replayed after lease expiry.
    """

    def __init__(self, *, max_queue_depth: int, clock: Clock = _utc_now) -> None:
        if max_queue_depth < 1:
            raise ValueError("max_queue_depth must be positive")
        self._max_queue_depth = max_queue_depth
        self._clock = clock
        self._items: dict[UUID, DurableWorkItem] = {}
        self._identities: dict[tuple[str, str, str], UUID] = {}
        self._leases: dict[UUID, FencedLease] = {}
        self._fence_tokens: dict[UUID, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def enqueue(self, item: DurableWorkItem) -> DurableWorkItem:
        if item.state is not WorkState.QUEUED or item.attempt != 0:
            raise ValueError("new work must be queued with attempt=0")
        identity = (item.tenant_id, item.queue, item.idempotency_key)
        async with self._lock:
            existing_id = self._identities.get(identity)
            if existing_id is not None:
                existing = self._items[existing_id]
                if (
                    existing.kind != item.kind
                    or existing.payload != item.payload
                    or existing.max_attempts != item.max_attempts
                ):
                    raise DuplicateActionError(
                        "durable work idempotency key already belongs to different work"
                    )
                return existing
            active = sum(
                1
                for current in self._items.values()
                if current.queue == item.queue
                and current.state in {WorkState.QUEUED, WorkState.LEASED}
            )
            if active >= self._max_queue_depth:
                raise QueueOverloadedError(
                    f"queue {item.queue!r} reached configured depth {self._max_queue_depth}"
                )
            self._items[item.job_id] = item
            self._identities[identity] = item.job_id
            return item

    async def acquire(
        self,
        *,
        queue: str,
        owner_id: str,
        lease_ttl_seconds: float,
        tenant_id: str | None = None,
    ) -> LeasedWorkItem | None:
        _require_positive_ttl(lease_ttl_seconds)
        if not queue.strip() or not owner_id.strip():
            raise ValueError("queue and owner must be non-blank")
        async with self._lock:
            now = self._clock()
            candidates = sorted(
                (
                    item
                    for item in self._items.values()
                    if item.queue == queue
                    and (tenant_id is None or item.tenant_id == tenant_id)
                    and self._eligible(item, now)
                ),
                key=lambda item: (item.available_at, item.created_at, str(item.job_id)),
            )
            for current in candidates:
                if current.attempt >= current.max_attempts:
                    dead = current.model_copy(
                        update={
                            "state": WorkState.DEAD_LETTER,
                            "last_error": current.last_error or "lease expired after final attempt",
                        }
                    )
                    self._items[current.job_id] = dead
                    self._leases.pop(current.job_id, None)
                    continue
                self._fence_tokens[current.job_id] += 1
                token = self._fence_tokens[current.job_id]
                lease = FencedLease(
                    lease_id=uuid4(),
                    tenant_id=current.tenant_id,
                    resource_key=current.resource_key,
                    owner_id=owner_id,
                    fence_token=token,
                    acquired_at=now,
                    expires_at=now + timedelta(seconds=lease_ttl_seconds),
                )
                leased_item = current.model_copy(
                    update={"state": WorkState.LEASED, "attempt": current.attempt + 1}
                )
                self._items[current.job_id] = leased_item
                self._leases[current.job_id] = lease
                return LeasedWorkItem(item=leased_item, lease=lease)
            return None

    async def complete(self, leased: LeasedWorkItem) -> DurableWorkItem:
        async with self._lock:
            current = self._require_current(leased)
            completed = current.model_copy(
                update={"state": WorkState.SUCCEEDED, "completed_at": self._clock()}
            )
            self._items[current.job_id] = completed
            self._leases.pop(current.job_id, None)
            return completed

    async def retry(
        self,
        leased: LeasedWorkItem,
        *,
        delay_seconds: float,
        error: str,
    ) -> DurableWorkItem:
        if delay_seconds < 0:
            raise ValueError("retry delay cannot be negative")
        if not error.strip():
            raise ValueError("retry error must be non-blank")
        async with self._lock:
            current = self._require_current(leased)
            if current.attempt >= current.max_attempts:
                updated = current.model_copy(
                    update={"state": WorkState.DEAD_LETTER, "last_error": error}
                )
            else:
                updated = current.model_copy(
                    update={
                        "state": WorkState.QUEUED,
                        "available_at": self._clock() + timedelta(seconds=delay_seconds),
                        "last_error": error,
                    }
                )
            self._items[current.job_id] = updated
            self._leases.pop(current.job_id, None)
            return updated

    async def snapshot(self, *, queue: str, tenant_id: str | None = None) -> QueueSnapshot:
        async with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.queue == queue and (tenant_id is None or item.tenant_id == tenant_id)
            ]
            return QueueSnapshot(
                queue=queue,
                tenant_id=tenant_id,
                queued=sum(item.state is WorkState.QUEUED for item in items),
                leased=sum(item.state is WorkState.LEASED for item in items),
                dead_letter=sum(item.state is WorkState.DEAD_LETTER for item in items),
                succeeded=sum(item.state is WorkState.SUCCEEDED for item in items),
                captured_at=self._clock(),
            )

    def _eligible(self, item: DurableWorkItem, now: datetime) -> bool:
        if item.state is WorkState.QUEUED:
            return item.available_at <= now
        if item.state is WorkState.LEASED:
            lease = self._leases.get(item.job_id)
            return lease is None or lease.is_expired(now)
        return False

    def _require_current(self, leased: LeasedWorkItem) -> DurableWorkItem:
        current = self._items.get(leased.item.job_id)
        active = self._leases.get(leased.item.job_id)
        now = self._clock()
        if (
            current is None
            or current.state is not WorkState.LEASED
            or current.attempt != leased.item.attempt
            or active is None
            or active.lease_id != leased.lease.lease_id
            or active.owner_id != leased.lease.owner_id
            or active.fence_token != leased.lease.fence_token
            or active.is_expired(now)
        ):
            raise LeaseLostError("work transition rejected because scheduler lease is stale")
        return current


class RuntimeState(StrEnum):
    ACCEPTING = "accepting"
    DRAINING = "draining"
    STOPPED = "stopped"


class RuntimeLifecycle:
    """Process-local admission/drain state used by stateless API and worker replicas."""

    def __init__(self) -> None:
        self._state = RuntimeState.ACCEPTING
        self._in_flight = 0
        self._condition = asyncio.Condition()

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def accepting(self) -> bool:
        return self._state is RuntimeState.ACCEPTING

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._state is not RuntimeState.ACCEPTING:
                raise RuntimeDrainingError("runtime is draining and is not accepting new work")
            self._in_flight += 1
        try:
            yield
        finally:
            async with self._condition:
                self._in_flight -= 1
                self._condition.notify_all()

    async def begin_draining(self) -> None:
        async with self._condition:
            if self._state is RuntimeState.ACCEPTING:
                self._state = RuntimeState.DRAINING
            self._condition.notify_all()

    async def wait_for_drain(self, *, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("drain timeout cannot be negative")
        async with self._condition:
            if self._in_flight == 0:
                return True
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._in_flight == 0),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                return False
            return True

    async def stop(self) -> None:
        async with self._condition:
            self._state = RuntimeState.STOPPED
            self._condition.notify_all()


class CapacityAdmissionController:
    """Immediate fail-fast backpressure for bounded replica-local concurrency."""

    def __init__(self, envelope: CapacityEnvelope) -> None:
        self._envelope = envelope
        self._total = 0
        self._per_tenant: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @property
    def in_flight(self) -> int:
        return self._total

    @asynccontextmanager
    async def admit(self, tenant_id: str) -> AsyncIterator[None]:
        async with self._lock:
            tenant_count = self._per_tenant[tenant_id]
            if self._total >= self._envelope.max_in_flight:
                raise CapacityExceededError("global in-flight capacity is exhausted")
            if tenant_count >= self._envelope.max_per_tenant_in_flight:
                raise CapacityExceededError("tenant in-flight capacity is exhausted")
            self._total += 1
            self._per_tenant[tenant_id] = tenant_count + 1
        try:
            yield
        finally:
            async with self._lock:
                self._total -= 1
                remaining = self._per_tenant[tenant_id] - 1
                if remaining:
                    self._per_tenant[tenant_id] = remaining
                else:
                    self._per_tenant.pop(tenant_id, None)


class RecoverableScheduler:
    """Provider-neutral worker loop over a durable fenced queue.

    Handlers receive the fencing lease. Any handler that can create an external side effect must
    make that effect fence-aware or provider-idempotent. This scheduler deliberately does not claim
    that an arbitrary external API becomes exactly-once merely because queue ownership is fenced.
    """

    def __init__(
        self,
        queue: DurableWorkQueue,
        *,
        queue_name: str,
        owner_id: str,
        lease_ttl_seconds: float,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        _require_positive_ttl(lease_ttl_seconds)
        if retry_delay_seconds < 0:
            raise ValueError("retry delay cannot be negative")
        self._queue = queue
        self._queue_name = queue_name
        self._owner_id = owner_id
        self._lease_ttl_seconds = lease_ttl_seconds
        self._retry_delay_seconds = retry_delay_seconds

    async def run_once(self, handler: WorkHandler, *, tenant_id: str | None = None) -> bool:
        leased = await self._queue.acquire(
            queue=self._queue_name,
            owner_id=self._owner_id,
            lease_ttl_seconds=self._lease_ttl_seconds,
            tenant_id=tenant_id,
        )
        if leased is None:
            return False
        try:
            await handler(leased)
        except Exception as exc:
            await self._queue.retry(
                leased,
                delay_seconds=self._retry_delay_seconds,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        await self._queue.complete(leased)
        return True
