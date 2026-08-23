from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from prodkit_control_core import (
    DurableWorkItem,
    DuplicateActionError,
    FencedLease,
    LeaseLostError,
    LeasedWorkItem,
    QueueOverloadedError,
    QueueSnapshot,
    WorkState,
)

from .models import Base


class WorkLeaseRow(Base):
    __tablename__ = "work_leases"
    __table_args__ = (
        CheckConstraint("fence_token >= 0", name="ck_work_leases_fence_token"),
        Index("ix_work_leases_expiry", "expires_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DurableWorkItemRow(Base):
    __tablename__ = "durable_work_items"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "queue", "idempotency_key", name="uq_durable_work_identity"
        ),
        CheckConstraint("attempt >= 0", name="ck_durable_work_attempt"),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 1000",
            name="ck_durable_work_max_attempts",
        ),
        CheckConstraint("lease_fence_token >= 0", name="ck_durable_work_fence_token"),
        Index("ix_durable_work_available", "queue", "state", "available_at", "created_at"),
        Index(
            "ix_durable_work_tenant_available",
            "tenant_id",
            "queue",
            "state",
            "available_at",
            "created_at",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    queue: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    lease_owner_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lease_fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime):
        raise RuntimeError("PostgreSQL did not return an aware database timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("PostgreSQL returned a naive database timestamp")
    return value


async def _advisory_lock(session: AsyncSession, identity: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": identity},
    )


class PostgresLeaseStore:
    """Database-clock fenced ownership safe for horizontally scaled replicas."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def acquire(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> FencedLease | None:
        self._validate_inputs(tenant_id, resource_key, owner_id, ttl_seconds)
        async with self._sessions.begin() as session:
            await _advisory_lock(session, f"lease:{tenant_id}:{resource_key}")
            now = await _database_now(session)
            row = await session.get(
                WorkLeaseRow,
                (tenant_id, resource_key),
                with_for_update=True,
            )
            if row is not None and row.lease_id is not None and row.expires_at is not None:
                if row.expires_at > now:
                    return self._lease(row) if row.owner_id == owner_id else None
            if row is None:
                row = WorkLeaseRow(
                    tenant_id=tenant_id,
                    resource_key=resource_key,
                    fence_token=1,
                    lease_id=uuid4(),
                    owner_id=owner_id,
                    acquired_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    updated_at=now,
                )
                session.add(row)
            else:
                row.fence_token += 1
                row.lease_id = uuid4()
                row.owner_id = owner_id
                row.acquired_at = now
                row.expires_at = now + timedelta(seconds=ttl_seconds)
                row.updated_at = now
            return self._lease(row)

    async def renew(self, lease: FencedLease, *, ttl_seconds: float) -> FencedLease:
        if ttl_seconds <= 0 or ttl_seconds > 86400:
            raise ValueError("lease TTL must be > 0 and <= 86400 seconds")
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            row = await session.get(
                WorkLeaseRow,
                (lease.tenant_id, lease.resource_key),
                with_for_update=True,
            )
            self._require_current(row, lease, now)
            assert row is not None
            row.expires_at = now + timedelta(seconds=ttl_seconds)
            row.updated_at = now
            return self._lease(row)

    async def release(self, lease: FencedLease) -> None:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            row = await session.get(
                WorkLeaseRow,
                (lease.tenant_id, lease.resource_key),
                with_for_update=True,
            )
            self._require_current(row, lease, now)
            assert row is not None
            row.lease_id = None
            row.owner_id = None
            row.acquired_at = None
            row.expires_at = None
            row.updated_at = now

    async def is_current(self, lease: FencedLease) -> bool:
        async with self._sessions() as session:
            now = await _database_now(session)
            row = await session.get(WorkLeaseRow, (lease.tenant_id, lease.resource_key))
            return self._matches(row, lease) and row is not None and row.expires_at is not None and row.expires_at > now

    @staticmethod
    def _validate_inputs(
        tenant_id: str, resource_key: str, owner_id: str, ttl_seconds: float
    ) -> None:
        if not tenant_id.strip() or not resource_key.strip() or not owner_id.strip():
            raise ValueError("lease tenant, resource, and owner must be non-blank")
        if ttl_seconds <= 0 or ttl_seconds > 86400:
            raise ValueError("lease TTL must be > 0 and <= 86400 seconds")

    @staticmethod
    def _matches(row: WorkLeaseRow | None, lease: FencedLease) -> bool:
        return (
            row is not None
            and row.lease_id == lease.lease_id
            and row.owner_id == lease.owner_id
            and row.fence_token == lease.fence_token
        )

    @classmethod
    def _require_current(
        cls,
        row: WorkLeaseRow | None,
        lease: FencedLease,
        now: datetime,
    ) -> None:
        if (
            not cls._matches(row, lease)
            or row is None
            or row.expires_at is None
            or row.expires_at <= now
        ):
            raise LeaseLostError("lease is stale, released, replaced, or expired")

    @staticmethod
    def _lease(row: WorkLeaseRow) -> FencedLease:
        if (
            row.lease_id is None
            or row.owner_id is None
            or row.acquired_at is None
            or row.expires_at is None
        ):
            raise RuntimeError("active work lease row is incomplete")
        return FencedLease(
            lease_id=row.lease_id,
            tenant_id=row.tenant_id,
            resource_key=row.resource_key,
            owner_id=row.owner_id,
            fence_token=row.fence_token,
            acquired_at=row.acquired_at,
            expires_at=row.expires_at,
        )


class PostgresDurableWorkQueue:
    """Bounded durable queue using SKIP LOCKED and per-work fencing for HA workers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_queue_depth: int,
    ) -> None:
        if max_queue_depth < 1:
            raise ValueError("max_queue_depth must be positive")
        self._sessions = session_factory
        self._max_queue_depth = max_queue_depth

    async def enqueue(self, item: DurableWorkItem) -> DurableWorkItem:
        if item.state is not WorkState.QUEUED or item.attempt != 0:
            raise ValueError("new work must be queued with attempt=0")
        async with self._sessions.begin() as session:
            await _advisory_lock(session, f"queue-admission:{item.queue}")
            existing = await session.scalar(
                select(DurableWorkItemRow).where(
                    DurableWorkItemRow.tenant_id == item.tenant_id,
                    DurableWorkItemRow.queue == item.queue,
                    DurableWorkItemRow.idempotency_key == item.idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.kind != item.kind
                    or existing.payload != item.payload
                    or existing.max_attempts != item.max_attempts
                ):
                    raise DuplicateActionError(
                        "durable work idempotency key already belongs to different work"
                    )
                return self._item(existing)
            active = await session.scalar(
                select(func.count())
                .select_from(DurableWorkItemRow)
                .where(
                    DurableWorkItemRow.queue == item.queue,
                    DurableWorkItemRow.state.in_(
                        (WorkState.QUEUED.value, WorkState.LEASED.value)
                    ),
                )
            )
            if int(active or 0) >= self._max_queue_depth:
                raise QueueOverloadedError(
                    f"queue {item.queue!r} reached configured depth {self._max_queue_depth}"
                )
            session.add(
                DurableWorkItemRow(
                    job_id=item.job_id,
                    tenant_id=item.tenant_id,
                    queue=item.queue,
                    kind=item.kind,
                    idempotency_key=item.idempotency_key,
                    state=item.state.value,
                    payload=item.payload,
                    created_at=item.created_at,
                    available_at=item.available_at,
                    attempt=item.attempt,
                    max_attempts=item.max_attempts,
                    lease_fence_token=0,
                    completed_at=item.completed_at,
                    last_error=item.last_error,
                )
            )
            return item

    async def acquire(
        self,
        *,
        queue: str,
        owner_id: str,
        lease_ttl_seconds: float,
        tenant_id: str | None = None,
    ) -> LeasedWorkItem | None:
        if not queue.strip() or not owner_id.strip():
            raise ValueError("queue and owner must be non-blank")
        if lease_ttl_seconds <= 0 or lease_ttl_seconds > 86400:
            raise ValueError("lease TTL must be > 0 and <= 86400 seconds")
        async with self._sessions.begin() as session:
            while True:
                now = await _database_now(session)
                eligibility = or_(
                    and_(
                        DurableWorkItemRow.state == WorkState.QUEUED.value,
                        DurableWorkItemRow.available_at <= now,
                    ),
                    and_(
                        DurableWorkItemRow.state == WorkState.LEASED.value,
                        DurableWorkItemRow.lease_expires_at.is_not(None),
                        DurableWorkItemRow.lease_expires_at <= now,
                    ),
                )
                statement = (
                    select(DurableWorkItemRow)
                    .where(DurableWorkItemRow.queue == queue, eligibility)
                    .order_by(DurableWorkItemRow.available_at, DurableWorkItemRow.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if tenant_id is not None:
                    statement = statement.where(DurableWorkItemRow.tenant_id == tenant_id)
                row = await session.scalar(statement)
                if row is None:
                    return None
                if row.attempt >= row.max_attempts:
                    row.state = WorkState.DEAD_LETTER.value
                    row.last_error = row.last_error or "lease expired after final attempt"
                    self._clear_lease(row)
                    continue
                row.lease_fence_token += 1
                row.lease_id = uuid4()
                row.lease_owner_id = owner_id
                row.lease_acquired_at = now
                row.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
                row.state = WorkState.LEASED.value
                row.attempt += 1
                return LeasedWorkItem(item=self._item(row), lease=self._work_lease(row))

    async def complete(self, leased: LeasedWorkItem) -> DurableWorkItem:
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            row = await session.get(DurableWorkItemRow, leased.item.job_id, with_for_update=True)
            self._require_current_work(row, leased, now)
            assert row is not None
            row.state = WorkState.SUCCEEDED.value
            row.completed_at = now
            self._clear_lease(row)
            return self._item(row)

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
        async with self._sessions.begin() as session:
            now = await _database_now(session)
            row = await session.get(DurableWorkItemRow, leased.item.job_id, with_for_update=True)
            self._require_current_work(row, leased, now)
            assert row is not None
            row.last_error = error
            if row.attempt >= row.max_attempts:
                row.state = WorkState.DEAD_LETTER.value
            else:
                row.state = WorkState.QUEUED.value
                row.available_at = now + timedelta(seconds=delay_seconds)
            self._clear_lease(row)
            return self._item(row)

    async def snapshot(self, *, queue: str, tenant_id: str | None = None) -> QueueSnapshot:
        async with self._sessions() as session:
            now = await _database_now(session)
            statement = (
                select(DurableWorkItemRow.state, func.count())
                .where(DurableWorkItemRow.queue == queue)
                .group_by(DurableWorkItemRow.state)
            )
            if tenant_id is not None:
                statement = statement.where(DurableWorkItemRow.tenant_id == tenant_id)
            counts = {str(state): int(count) for state, count in (await session.execute(statement)).all()}
            return QueueSnapshot(
                queue=queue,
                tenant_id=tenant_id,
                queued=counts.get(WorkState.QUEUED.value, 0),
                leased=counts.get(WorkState.LEASED.value, 0),
                dead_letter=counts.get(WorkState.DEAD_LETTER.value, 0),
                succeeded=counts.get(WorkState.SUCCEEDED.value, 0),
                captured_at=now,
            )

    @staticmethod
    def _item(row: DurableWorkItemRow) -> DurableWorkItem:
        return DurableWorkItem(
            job_id=row.job_id,
            tenant_id=row.tenant_id,
            queue=row.queue,
            kind=row.kind,
            idempotency_key=row.idempotency_key,
            payload=row.payload,
            created_at=row.created_at,
            available_at=row.available_at,
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            state=WorkState(row.state),
            completed_at=row.completed_at,
            last_error=row.last_error,
        )

    @classmethod
    def _work_lease(cls, row: DurableWorkItemRow) -> FencedLease:
        if (
            row.lease_id is None
            or row.lease_owner_id is None
            or row.lease_acquired_at is None
            or row.lease_expires_at is None
        ):
            raise RuntimeError("leased work row has incomplete lease metadata")
        item = cls._item(row)
        return FencedLease(
            lease_id=row.lease_id,
            tenant_id=row.tenant_id,
            resource_key=item.resource_key,
            owner_id=row.lease_owner_id,
            fence_token=row.lease_fence_token,
            acquired_at=row.lease_acquired_at,
            expires_at=row.lease_expires_at,
        )

    @classmethod
    def _require_current_work(
        cls,
        row: DurableWorkItemRow | None,
        leased: LeasedWorkItem,
        now: datetime,
    ) -> None:
        if (
            row is None
            or row.state != WorkState.LEASED.value
            or row.tenant_id != leased.item.tenant_id
            or row.queue != leased.item.queue
            or row.attempt != leased.item.attempt
            or row.lease_id != leased.lease.lease_id
            or row.lease_owner_id != leased.lease.owner_id
            or row.lease_fence_token != leased.lease.fence_token
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise LeaseLostError("work transition rejected because scheduler lease is stale")

    @staticmethod
    def _clear_lease(row: DurableWorkItemRow) -> None:
        row.lease_id = None
        row.lease_owner_id = None
        row.lease_acquired_at = None
        row.lease_expires_at = None
