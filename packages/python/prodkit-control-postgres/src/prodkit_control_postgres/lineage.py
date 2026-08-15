from __future__ import annotations

from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    IntegrityViolationError,
    LineageGraph,
    LineageNode,
    LineageRelation,
)

from .models import LineageNodeRow, LineageRelationRow

_LINEAGE_NODE_ADAPTER: TypeAdapter[LineageNode] = TypeAdapter(LineageNode)


class PostgresLineageStore:
    """Append-only PostgreSQL persistence for validated lineage graphs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record_node(self, node: LineageNode) -> None:
        document = node.model_dump(mode="json")
        async with self._sessions.begin() as session:
            await self._lock_run(session, node.run_id)
            existing = await session.get(LineageNodeRow, node.node_id)
            if existing is not None:
                if existing.document != document:
                    raise IntegrityViolationError("a lineage node id cannot be rewritten")
                return
            session.add(
                LineageNodeRow(
                    node_id=node.node_id,
                    run_id=node.run_id,
                    tenant_id=node.tenant_id,
                    kind=node.kind.value,
                    digest=node.digest,
                    recorded_at=node.recorded_at,
                    document=document,
                )
            )

    async def record_relation(self, run_id: UUID, relation: LineageRelation) -> None:
        async with self._sessions.begin() as session:
            await self._lock_run(session, run_id)
            graph = await self._get_graph(session, run_id)
            existing = await session.scalar(
                select(LineageRelationRow).where(
                    LineageRelationRow.run_id == run_id,
                    LineageRelationRow.relation == relation.relation.value,
                    LineageRelationRow.subject_node_id == relation.subject.node_id,
                    LineageRelationRow.object_node_id == relation.object.node_id,
                )
            )
            if existing is not None:
                if existing.document != relation.model_dump(mode="json"):
                    raise IntegrityViolationError("a lineage relation cannot be rewritten")
                return
            validated = LineageGraph(
                run_id=graph.run_id,
                tenant_id=graph.tenant_id,
                nodes=graph.nodes,
                relations=(*graph.relations, relation),
            )
            session.add(
                LineageRelationRow(
                    run_id=run_id,
                    tenant_id=validated.tenant_id,
                    relation=relation.relation.value,
                    subject_node_id=relation.subject.node_id,
                    object_node_id=relation.object.node_id,
                    recorded_at=relation.recorded_at,
                    document=relation.model_dump(mode="json"),
                )
            )

    async def get_graph(self, run_id: UUID) -> LineageGraph:
        async with self._sessions() as session:
            return await self._get_graph(session, run_id)

    @staticmethod
    async def _lock_run(session: AsyncSession, run_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:run_id, 0))"),
            {"run_id": str(run_id)},
        )

    @staticmethod
    async def _get_graph(session: AsyncSession, run_id: UUID) -> LineageGraph:
        node_rows = (
            await session.scalars(
                select(LineageNodeRow)
                .where(LineageNodeRow.run_id == run_id)
                .order_by(LineageNodeRow.recorded_at, LineageNodeRow.node_id)
            )
        ).all()
        if not node_rows:
            raise KeyError(f"lineage run {run_id} does not exist")
        relation_rows = (
            await session.scalars(
                select(LineageRelationRow)
                .where(LineageRelationRow.run_id == run_id)
                .order_by(LineageRelationRow.id)
            )
        ).all()
        nodes = tuple(_LINEAGE_NODE_ADAPTER.validate_python(row.document) for row in node_rows)
        relations = tuple(LineageRelation.model_validate(row.document) for row in relation_rows)
        return LineageGraph(
            run_id=run_id,
            tenant_id=nodes[0].tenant_id,
            nodes=nodes,
            relations=relations,
        )
