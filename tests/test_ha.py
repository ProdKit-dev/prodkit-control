from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prodkit_control_core import (
    CapacityEnvelope,
    CapacityExceededError,
    DurableWorkItem,
    DuplicateActionError,
    LeaseLostError,
    LeasedWorkItem,
    QueueOverloadedError,
    RuntimeDrainingError,
    WorkState,
)
from prodkit_control_runtime import (
    CapacityAdmissionController,
    InMemoryDurableWorkQueue,
    InMemoryLeaseStore,
    RuntimeLifecycle,
    RuntimeState,
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _item(
    clock: MutableClock,
    *,
    queue: str = "control",
    tenant_id: str = "tenant-a",
    key: str = "job-1",
    ordinal: int = 1,
    max_attempts: int = 3,
) -> DurableWorkItem:
    return DurableWorkItem(
        job_id=uuid4(),
        tenant_id=tenant_id,
        queue=queue,
        kind="reconcile",
        idempotency_key=key,
        payload={"ordinal": ordinal},
        created_at=clock(),
        available_at=clock(),
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_fenced_lease_has_single_owner_and_monotonic_takeover() -> None:
    clock = MutableClock(datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    leases = InMemoryLeaseStore(clock=clock)
    contenders = await asyncio.gather(
        *(
            leases.acquire(
                tenant_id="tenant-a",
                resource_key="scheduler:reconcile",
                owner_id=f"replica-{index}",
                ttl_seconds=30,
            )
            for index in range(100)
        )
    )
    winners = [lease for lease in contenders if lease is not None]
    assert len(winners) == 1
    first = winners[0]
    assert first.fence_token == 1
    assert await leases.is_current(first)

    clock.advance(31)
    replacement = await leases.acquire(
        tenant_id="tenant-a",
        resource_key="scheduler:reconcile",
        owner_id="replacement",
        ttl_seconds=30,
    )
    assert replacement is not None
    assert replacement.fence_token == 2
    assert not await leases.is_current(first)
    with pytest.raises(LeaseLostError):
        await leases.release(first)
    renewed = await leases.renew(replacement, ttl_seconds=60)
    assert renewed.fence_token == replacement.fence_token
    assert renewed.expires_at > replacement.expires_at


@pytest.mark.asyncio
async def test_bounded_queue_is_idempotent_and_rejects_overload() -> None:
    clock = MutableClock(datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    queue = InMemoryDurableWorkQueue(max_queue_depth=2, clock=clock)
    first = _item(clock, key="one", ordinal=1)
    second = _item(clock, key="two", ordinal=2)
    assert await queue.enqueue(first) == first
    assert await queue.enqueue(first) == first
    conflicting_retry_policy = first.model_copy(update={"max_attempts": first.max_attempts + 1})
    with pytest.raises(DuplicateActionError):
        await queue.enqueue(conflicting_retry_policy)
    await queue.enqueue(second)

    with pytest.raises(QueueOverloadedError):
        await queue.enqueue(_item(clock, key="three", ordinal=3))
    snapshot = await queue.snapshot(queue="control", tenant_id="tenant-a")
    assert snapshot.active_depth == 2
    assert snapshot.queued == 2
    foreign = await queue.snapshot(queue="control", tenant_id="tenant-b")
    assert foreign.active_depth == 0


@pytest.mark.asyncio
async def test_known_foreign_tenant_cannot_acquire_queued_work() -> None:
    clock = MutableClock(datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    queue = InMemoryDurableWorkQueue(max_queue_depth=4, clock=clock)
    item = _item(clock, tenant_id="tenant-a")
    await queue.enqueue(item)
    assert (
        await queue.acquire(
            queue="control",
            owner_id="foreign-worker",
            lease_ttl_seconds=30,
            tenant_id="tenant-b",
        )
        is None
    )
    leased = await queue.acquire(
        queue="control",
        owner_id="tenant-worker",
        lease_ttl_seconds=30,
        tenant_id="tenant-a",
    )
    assert leased is not None and leased.item.job_id == item.job_id


@pytest.mark.asyncio
async def test_failover_rejects_stale_worker_and_does_not_duplicate_effect() -> None:
    clock = MutableClock(datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    queue = InMemoryDurableWorkQueue(max_queue_depth=8, clock=clock)
    item = _item(clock, max_attempts=3)
    await queue.enqueue(item)

    effects: dict[str, int] = {}
    latest_fence: dict[str, int] = {}

    async def apply_external_effect(leased: LeasedWorkItem) -> None:
        key = leased.item.idempotency_key
        fence = leased.lease.fence_token
        if key in effects:
            return
        if fence <= latest_fence.get(leased.lease.resource_key, 0):
            raise LeaseLostError("external sink rejected stale fencing token")
        latest_fence[leased.lease.resource_key] = fence
        effects[key] = effects.get(key, 0) + 1

    first = await queue.acquire(
        queue="control",
        owner_id="replica-a",
        lease_ttl_seconds=10,
        tenant_id="tenant-a",
    )
    assert first is not None
    await apply_external_effect(first)
    clock.advance(11)

    second = await queue.acquire(
        queue="control",
        owner_id="replica-b",
        lease_ttl_seconds=10,
        tenant_id="tenant-a",
    )
    assert second is not None
    assert second.item.job_id == first.item.job_id
    assert second.lease.fence_token > first.lease.fence_token
    await apply_external_effect(second)
    await queue.complete(second)

    assert effects == {item.idempotency_key: 1}
    with pytest.raises(LeaseLostError):
        await queue.complete(first)


@pytest.mark.asyncio
async def test_retry_is_bounded_and_dead_letters() -> None:
    clock = MutableClock(datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    queue = InMemoryDurableWorkQueue(max_queue_depth=4, clock=clock)
    item = _item(clock, max_attempts=2)
    await queue.enqueue(item)

    first = await queue.acquire(
        queue="control", owner_id="worker-a", lease_ttl_seconds=30, tenant_id="tenant-a"
    )
    assert first is not None and first.item.attempt == 1
    retried = await queue.retry(first, delay_seconds=5, error="transient provider failure")
    assert retried.state is WorkState.QUEUED
    assert (
        await queue.acquire(
            queue="control",
            owner_id="too-early",
            lease_ttl_seconds=30,
            tenant_id="tenant-a",
        )
        is None
    )

    clock.advance(5)
    second = await queue.acquire(
        queue="control", owner_id="worker-b", lease_ttl_seconds=30, tenant_id="tenant-a"
    )
    assert second is not None and second.item.attempt == 2
    dead = await queue.retry(second, delay_seconds=5, error="retry budget exhausted")
    assert dead.state is WorkState.DEAD_LETTER
    assert dead.last_error == "retry budget exhausted"
    assert (
        await queue.acquire(
            queue="control",
            owner_id="worker-c",
            lease_ttl_seconds=30,
            tenant_id="tenant-a",
        )
        is None
    )


@pytest.mark.asyncio
async def test_capacity_admission_is_global_and_tenant_bounded() -> None:
    envelope = CapacityEnvelope(
        profile_id="test",
        max_queue_depth=10,
        max_in_flight=2,
        max_per_tenant_in_flight=1,
        lease_ttl_seconds=30,
        shutdown_grace_seconds=10,
        qualification_concurrency=2,
        qualification_work_items=10,
        qualification_soak_seconds=1,
    )
    capacity = CapacityAdmissionController(envelope)
    async with capacity.admit("tenant-a"):
        with pytest.raises(CapacityExceededError):
            async with capacity.admit("tenant-a"):
                pass
        async with capacity.admit("tenant-b"):
            assert capacity.in_flight == 2
            with pytest.raises(CapacityExceededError):
                async with capacity.admit("tenant-c"):
                    pass
    assert capacity.in_flight == 0


@pytest.mark.asyncio
async def test_runtime_drain_rejects_new_work_and_waits_for_inflight() -> None:
    lifecycle = RuntimeLifecycle()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_request() -> None:
        async with lifecycle.admit():
            entered.set()
            await release.wait()

    task = asyncio.create_task(active_request())
    await entered.wait()
    assert lifecycle.in_flight == 1
    await lifecycle.begin_draining()
    assert lifecycle.state is RuntimeState.DRAINING
    with pytest.raises(RuntimeDrainingError):
        async with lifecycle.admit():
            pass

    drain = asyncio.create_task(lifecycle.wait_for_drain(timeout_seconds=1))
    await asyncio.sleep(0)
    assert not drain.done()
    release.set()
    await task
    assert await drain
    await lifecycle.stop()
    assert lifecycle.state is RuntimeState.STOPPED
