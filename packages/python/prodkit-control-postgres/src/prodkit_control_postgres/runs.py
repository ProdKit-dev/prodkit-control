from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from prodkit_control_core import RunRecord

from .models import Base

SCHEMA_VERSION = 5


class RunRow(Base):
    __tablename__ = "control_runs"
    __table_args__ = (Index("ix_control_runs_tenant_status", "tenant_id", "status", "started_at"),)

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class PostgresRunStore:
    """Durable current-state projection for control runs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, run: RunRecord) -> None:
        async with self._sessions.begin() as session:
            if await session.get(RunRow, run.run_id) is not None:
                raise ValueError(f"run {run.run_id} already exists")
            session.add(
                RunRow(
                    run_id=run.run_id,
                    tenant_id=run.tenant_id,
                    status=run.status.value,
                    started_at=run.started_at,
                    document=run.model_dump(mode="json"),
                )
            )

    async def replace(self, run: RunRecord) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(RunRow, run.run_id, with_for_update=True)
            if row is None:
                raise KeyError(run.run_id)
            if row.tenant_id != run.tenant_id or row.started_at != run.started_at:
                raise ValueError("run identity is immutable")
            row.status = run.status.value
            row.document = run.model_dump(mode="json")

    async def get(self, run_id: UUID) -> RunRecord | None:
        async with self._sessions() as session:
            row = await session.get(RunRow, run_id)
            return RunRecord.model_validate(row.document) if row is not None else None


async def assert_schema_compatible(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    expected: int = SCHEMA_VERSION,
) -> None:
    """Fail closed when the database schema is missing, ahead, or behind this runtime."""

    async with _sessions_for_check(session_factory) as session:
        version = await session.scalar(
            text("SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE")
        )
    if version is None:
        raise RuntimeError("ProdKit Control schema metadata is missing; run migrations")
    if int(version) != expected:
        raise RuntimeError(
            f"ProdKit Control schema version {version} is incompatible; expected {expected}"
        )


class _SchemaSession:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        self._session = self._session_factory()
        return self._session

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._session is not None
        await self._session.close()


def _sessions_for_check(
    session_factory: async_sessionmaker[AsyncSession],
) -> _SchemaSession:
    return _SchemaSession(session_factory)
