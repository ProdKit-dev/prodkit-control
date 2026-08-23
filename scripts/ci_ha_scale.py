from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from prodkit_control_core import (
    DurableWorkItem,
    LeaseLostError,
    LeasedWorkItem,
    QueueOverloadedError,
)
from prodkit_control_runtime import (
    InMemoryDurableWorkQueue,
    InMemoryLeaseStore,
    REFERENCE_CAPACITY_ENVELOPE,
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


async def _qualify_lease_race() -> None:
    leases = InMemoryLeaseStore()
    contenders = await asyncio.gather(
        *(
            leases.acquire(
                tenant_id="qualification",
                resource_key="scheduler:leader",
                owner_id=f"replica-{index}",
                ttl_seconds=REFERENCE_CAPACITY_ENVELOPE.lease_ttl_seconds,
            )
            for index in range(REFERENCE_CAPACITY_ENVELOPE.qualification_concurrency)
        )
    )
    winners = [lease for lease in contenders if lease is not None]
    if len(winners) != 1:
        raise AssertionError(f"fencing race elected {len(winners)} owners instead of one")


async def _qualify_queue_load() -> None:
    envelope = REFERENCE_CAPACITY_ENVELOPE
    queue = InMemoryDurableWorkQueue(max_queue_depth=envelope.max_queue_depth)
    created = datetime.now(UTC)
    tenant_count = 32
    for ordinal in range(envelope.qualification_work_items):
        await queue.enqueue(
            DurableWorkItem(
                job_id=uuid4(),
                tenant_id=f"tenant-{ordinal % tenant_count}",
                queue="qualification",
                kind="load",
                idempotency_key=f"qualification-{ordinal}",
                payload={"ordinal": ordinal},
                created_at=created,
                available_at=created,
                max_attempts=2,
            )
        )

    if envelope.qualification_work_items == envelope.max_queue_depth:
        try:
            await queue.enqueue(
                DurableWorkItem(
                    job_id=uuid4(),
                    tenant_id="overflow",
                    queue="qualification",
                    kind="load",
                    idempotency_key="overflow",
                    payload={},
                    created_at=created,
                    available_at=created,
                )
            )
        except QueueOverloadedError:
            pass
        else:
            raise AssertionError("queue accepted work beyond the published envelope")

    async def worker(index: int) -> int:
        completed = 0
        tenant_id = f"tenant-{index % tenant_count}"
        while True:
            leased = await queue.acquire(
                queue="qualification",
                owner_id=f"worker-{index}",
                lease_ttl_seconds=envelope.lease_ttl_seconds,
                tenant_id=tenant_id,
            )
            if leased is None:
                return completed
            if leased.item.tenant_id != tenant_id:
                raise AssertionError("worker acquired foreign-tenant work")
            await queue.complete(leased)
            completed += 1

    counts = await asyncio.gather(
        *(worker(index) for index in range(envelope.qualification_concurrency))
    )
    if sum(counts) != envelope.qualification_work_items:
        raise AssertionError("load qualification lost or duplicated queued work")
    succeeded = 0
    active = 0
    for index in range(tenant_count):
        snapshot = await queue.snapshot(
            queue="qualification", tenant_id=f"tenant-{index}"
        )
        succeeded += snapshot.succeeded
        active += snapshot.active_depth
    if succeeded != envelope.qualification_work_items or active != 0:
        raise AssertionError(
            f"unexpected tenant-partitioned terminal queue totals: succeeded={succeeded} active={active}"
        )
    foreign = await queue.snapshot(queue="qualification", tenant_id="foreign-tenant")
    if foreign.active_depth or foreign.succeeded:
        raise AssertionError("foreign tenant observed queue state")


async def _qualify_failover_no_duplicate_effect() -> None:
    clock = MutableClock(datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    queue = InMemoryDurableWorkQueue(max_queue_depth=4, clock=clock)
    item = DurableWorkItem(
        job_id=uuid4(),
        tenant_id="qualification",
        queue="failover",
        kind="external-effect",
        idempotency_key="stable-provider-key",
        payload={"operation": "create"},
        created_at=clock(),
        available_at=clock(),
        max_attempts=3,
    )
    await queue.enqueue(item)

    applied: set[str] = set()

    async def idempotent_fenced_sink(leased: LeasedWorkItem) -> None:
        applied.add(leased.item.idempotency_key)

    first = await queue.acquire(
        queue="failover",
        owner_id="replica-a",
        lease_ttl_seconds=5,
        tenant_id="qualification",
    )
    if first is None:
        raise AssertionError("first failover owner was not elected")
    await idempotent_fenced_sink(first)
    clock.advance(6)
    second = await queue.acquire(
        queue="failover",
        owner_id="replica-b",
        lease_ttl_seconds=5,
        tenant_id="qualification",
    )
    if second is None or second.lease.fence_token <= first.lease.fence_token:
        raise AssertionError("failover did not issue a higher fencing token")
    await idempotent_fenced_sink(second)
    await queue.complete(second)
    try:
        await queue.complete(first)
    except LeaseLostError:
        pass
    else:
        raise AssertionError("stale failover owner was allowed to acknowledge work")
    if applied != {item.idempotency_key}:
        raise AssertionError("failover duplicated the external-effect identity")


async def _qualify_soak() -> int:
    envelope = REFERENCE_CAPACITY_ENVELOPE
    leases = InMemoryLeaseStore()
    stop_at = monotonic() + envelope.qualification_soak_seconds

    async def replica(index: int) -> int:
        count = 0
        resource = f"soak:{index}"
        while monotonic() < stop_at:
            lease = await leases.acquire(
                tenant_id=f"tenant-{index % 32}",
                resource_key=resource,
                owner_id=f"replica-{index}",
                ttl_seconds=envelope.lease_ttl_seconds,
            )
            if lease is None:
                raise AssertionError("uncontended soak lease was unavailable")
            await leases.release(lease)
            count += 1
            if count % 100 == 0:
                await asyncio.sleep(0)
        return count

    operations = sum(
        await asyncio.gather(
            *(replica(index) for index in range(envelope.qualification_concurrency))
        )
    )
    if operations <= 0:
        raise AssertionError("soak qualification performed no operations")
    return operations


async def main() -> None:
    started = monotonic()
    await _qualify_lease_race()
    await _qualify_queue_load()
    await _qualify_failover_no_duplicate_effect()
    soak_operations = await _qualify_soak()
    envelope = REFERENCE_CAPACITY_ENVELOPE
    print(
        json.dumps(
            {
                "status": "qualified",
                "profile": envelope.profile_id,
                "max_queue_depth": envelope.max_queue_depth,
                "max_in_flight": envelope.max_in_flight,
                "max_per_tenant_in_flight": envelope.max_per_tenant_in_flight,
                "qualification_concurrency": envelope.qualification_concurrency,
                "qualification_work_items": envelope.qualification_work_items,
                "qualification_soak_seconds": envelope.qualification_soak_seconds,
                "soak_operations": soak_operations,
                "tenant_partitions": 32,
                "elapsed_seconds": round(monotonic() - started, 3),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
