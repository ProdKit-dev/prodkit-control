from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ControlEventRow(Base):
    __tablename__ = "control_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_control_events_run_sequence"),
        UniqueConstraint("event_id", name="uq_control_events_event_id"),
        Index("ix_control_events_tenant_recorded", "tenant_id", "recorded_at"),
        Index("ix_control_events_action", "action_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class LineageNodeRow(Base):
    __tablename__ = "lineage_nodes"
    __table_args__ = (
        Index("ix_lineage_nodes_run_kind", "run_id", "kind"),
        Index("ix_lineage_nodes_tenant_recorded", "tenant_id", "recorded_at"),
    )

    node_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class LineageRelationRow(Base):
    __tablename__ = "lineage_relations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "relation",
            "subject_node_id",
            "object_node_id",
            name="uq_lineage_relations_edge",
        ),
        Index("ix_lineage_relations_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lineage_nodes.node_id"), nullable=False
    )
    object_node_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lineage_nodes.node_id"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class IdempotencyRow(Base):
    """Durable ownership of one tenant-scoped externally intended action."""

    __tablename__ = "idempotency_claims"
    __table_args__ = (
        CheckConstraint("state IN ('claimed', 'completed')", name="ck_idempotency_state"),
        Index("ix_idempotency_tenant_state", "tenant_id", "state", "claimed_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


class ExecutionAttemptRow(Base):
    """Durable execution-attempt journal; identity columns never change after insertion."""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('claimed', 'started', 'succeeded', 'failed', 'uncertain')",
            name="ck_execution_attempt_state",
        ),
        Index("ix_execution_attempt_action", "action_id", "claimed_at"),
        Index("ix_execution_attempt_tenant_state", "tenant_id", "state", "claimed_at"),
        Index("ix_execution_attempt_idempotency", "tenant_id", "idempotency_key"),
    )

    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    action_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    executor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


APPEND_ONLY_DDL = text(
    """
    CREATE OR REPLACE FUNCTION prodkit_reject_append_only_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
    END;
    $$;

    DROP TRIGGER IF EXISTS control_events_no_update_delete ON control_events;
    CREATE TRIGGER control_events_no_update_delete
    BEFORE UPDATE OR DELETE ON control_events
    FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

    DROP TRIGGER IF EXISTS lineage_nodes_no_update_delete ON lineage_nodes;
    CREATE TRIGGER lineage_nodes_no_update_delete
    BEFORE UPDATE OR DELETE ON lineage_nodes
    FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

    DROP TRIGGER IF EXISTS lineage_relations_no_update_delete ON lineage_relations;
    CREATE TRIGGER lineage_relations_no_update_delete
    BEFORE UPDATE OR DELETE ON lineage_relations
    FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();
    """
)
