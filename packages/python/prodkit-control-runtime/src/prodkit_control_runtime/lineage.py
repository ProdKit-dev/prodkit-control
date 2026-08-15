from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from prodkit_control_core import (
    AgentActionNode,
    AuthorizationNode,
    BuildArtifactNode,
    DecisionSetNode,
    DeploymentNode,
    GeneratorConfigurationNode,
    IncompleteLineageError,
    IntegrityViolationError,
    LineageGraph,
    LineageNode,
    LineageNodeBase,
    LineageNodeRef,
    LineageRelation,
    LineageRelationType,
    ProductionLineageAssessment,
    ProductionLineageRequirement,
    ProductionObservationNode,
    ReconciliationNode,
    ReconciliationOutcome,
    SourceTreeNode,
    SpecificationRevisionNode,
    VerificationNode,
    VerificationOutcome,
)

LineageNodeT = TypeVar("LineageNodeT", bound=LineageNodeBase)


class InMemoryLineageStore:
    """Concurrency-safe lineage store for local use and contract testing."""

    def __init__(self) -> None:
        self._nodes: dict[UUID, dict[UUID, LineageNode]] = defaultdict(dict)
        self._relations: dict[UUID, list[LineageRelation]] = defaultdict(list)
        self._tenants: dict[UUID, str] = {}
        self._locks: dict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def record_node(self, node: LineageNode) -> None:
        async with self._locks[node.run_id]:
            tenant = self._tenants.setdefault(node.run_id, node.tenant_id)
            if tenant != node.tenant_id:
                raise IntegrityViolationError("a lineage run cannot cross tenant boundaries")
            existing = self._nodes[node.run_id].get(node.node_id)
            if existing is not None and existing != node:
                raise IntegrityViolationError("a lineage node id cannot be rewritten")
            self._nodes[node.run_id][node.node_id] = node

    async def record_relation(self, run_id: UUID, relation: LineageRelation) -> None:
        async with self._locks[run_id]:
            for existing in self._relations[run_id]:
                if (
                    existing.relation == relation.relation
                    and existing.subject == relation.subject
                    and existing.object == relation.object
                ):
                    if existing != relation:
                        raise IntegrityViolationError("a lineage relation cannot be rewritten")
                    return
            graph = self._graph(run_id, (*self._relations[run_id], relation))
            self._relations[run_id] = list(graph.relations)

    async def get_graph(self, run_id: UUID) -> LineageGraph:
        async with self._locks[run_id]:
            return self._graph(run_id, tuple(self._relations[run_id]))

    def _graph(
        self,
        run_id: UUID,
        relations: tuple[LineageRelation, ...],
    ) -> LineageGraph:
        tenant_id = self._tenants.get(run_id)
        if tenant_id is None:
            raise KeyError(f"lineage run {run_id} does not exist")
        return LineageGraph(
            run_id=run_id,
            tenant_id=tenant_id,
            nodes=tuple(self._nodes[run_id].values()),
            relations=relations,
        )


class ProductionLineagePolicy:
    """Fail-closed policy for accepting an observed production state."""

    def assess(
        self,
        graph: LineageGraph,
        observation: UUID | LineageNodeRef,
    ) -> ProductionLineageAssessment:
        observation_id = (
            observation.node_id if isinstance(observation, LineageNodeRef) else observation
        )
        nodes: dict[UUID, LineageNodeBase] = {node.node_id: node for node in graph.nodes}
        observed = nodes.get(observation_id)
        if not isinstance(observed, ProductionObservationNode):
            raise ValueError("production observation is not present in the lineage graph")
        if isinstance(observation, LineageNodeRef) and observed.ref != observation:
            raise ValueError("production observation reference does not match the graph")

        satisfied: list[ProductionLineageRequirement] = [
            ProductionLineageRequirement.PRODUCTION_OBSERVATION
        ]
        missing: list[ProductionLineageRequirement] = []

        deployment = self._subject(
            graph,
            nodes,
            LineageRelationType.OBSERVED_AS,
            observed,
            DeploymentNode,
            lambda node: node.succeeded,
        )
        self._mark(
            deployment is not None and deployment.succeeded,
            ProductionLineageRequirement.SUCCESSFUL_DEPLOYMENT,
            satisfied,
            missing,
        )
        action = self._subject(
            graph,
            nodes,
            LineageRelationType.DEPLOYED_AS,
            deployment,
            AgentActionNode,
            lambda node: node.succeeded,
        )
        self._mark(
            action is not None and action.succeeded,
            ProductionLineageRequirement.SUCCESSFUL_AGENT_ACTION,
            satisfied,
            missing,
        )
        authorization = self._subject(
            graph,
            nodes,
            LineageRelationType.AUTHORIZED_ACTION,
            action,
            AuthorizationNode,
            lambda node: node.authorized,
        )
        self._mark(
            authorization is not None and authorization.authorized,
            ProductionLineageRequirement.AUTHORIZATION,
            satisfied,
            missing,
        )
        build = self._subject(
            graph,
            nodes,
            LineageRelationType.AUTHORIZED_BY,
            authorization,
            BuildArtifactNode,
            lambda node: node.succeeded,
        )
        self._mark(
            build is not None and build.succeeded,
            ProductionLineageRequirement.SUCCESSFUL_BUILD,
            satisfied,
            missing,
        )
        source_tree = self._subject(
            graph,
            nodes,
            LineageRelationType.BUILT_AS,
            build,
            SourceTreeNode,
        )
        self._mark(
            source_tree is not None,
            ProductionLineageRequirement.SOURCE_TREE,
            satisfied,
            missing,
        )
        generator = self._subject(
            graph,
            nodes,
            LineageRelationType.PRODUCED,
            source_tree,
            GeneratorConfigurationNode,
            lambda node: node.succeeded,
        )
        self._mark(
            generator is not None and generator.succeeded,
            ProductionLineageRequirement.SUCCESSFUL_GENERATION,
            satisfied,
            missing,
        )
        verification = self._object(
            graph,
            nodes,
            LineageRelationType.VERIFIED_BY,
            source_tree,
            VerificationNode,
            lambda node: node.outcome is VerificationOutcome.PASSED,
        )
        self._mark(
            verification is not None and verification.outcome is VerificationOutcome.PASSED,
            ProductionLineageRequirement.PASSING_VERIFICATION,
            satisfied,
            missing,
        )
        specification = self._object(
            graph,
            nodes,
            LineageRelationType.GENERATED_FROM,
            generator,
            SpecificationRevisionNode,
        )
        self._mark(
            specification is not None,
            ProductionLineageRequirement.SPECIFICATION_REVISION,
            satisfied,
            missing,
        )
        decisions = self._object(
            graph,
            nodes,
            LineageRelationType.GENERATED_FROM,
            generator,
            DecisionSetNode,
        )
        self._mark(
            decisions is not None,
            ProductionLineageRequirement.DECISION_SET,
            satisfied,
            missing,
        )
        reconciliation = self._object(
            graph,
            nodes,
            LineageRelationType.COMPARED_BY,
            observed,
            ReconciliationNode,
            lambda node: node.outcome is ReconciliationOutcome.MATCHED,
        )
        self._mark(
            reconciliation is not None and reconciliation.outcome is ReconciliationOutcome.MATCHED,
            ProductionLineageRequirement.MATCHED_RECONCILIATION,
            satisfied,
            missing,
        )

        chronological = (
            specification,
            decisions,
            generator,
            source_tree,
            verification,
            build,
            authorization,
            action,
            deployment,
            observed,
            reconciliation,
        )
        return ProductionLineageAssessment(
            observation=observed.ref,
            complete=not missing,
            satisfied_requirements=tuple(satisfied),
            missing_requirements=tuple(missing),
            lineage_path=tuple(node.ref for node in chronological if node is not None),
        )

    def enforce(
        self,
        graph: LineageGraph,
        observation: UUID | LineageNodeRef,
    ) -> ProductionLineageAssessment:
        assessment = self.assess(graph, observation)
        if not assessment.complete:
            raise IncompleteLineageError(
                tuple(requirement.value for requirement in assessment.missing_requirements)
            )
        return assessment

    @staticmethod
    def _mark(
        condition: bool,
        requirement: ProductionLineageRequirement,
        satisfied: list[ProductionLineageRequirement],
        missing: list[ProductionLineageRequirement],
    ) -> None:
        (satisfied if condition else missing).append(requirement)

    @staticmethod
    def _subject(
        graph: LineageGraph,
        nodes: dict[UUID, LineageNodeBase],
        relation_type: LineageRelationType,
        object_node: LineageNodeBase | None,
        expected_type: type[LineageNodeT],
        predicate: Callable[[LineageNodeT], bool] | None = None,
    ) -> LineageNodeT | None:
        if object_node is None:
            return None
        candidates: list[LineageNodeT] = []
        for relation in graph.relations:
            if relation.relation is relation_type and relation.object == object_node.ref:
                candidate = nodes[relation.subject.node_id]
                if isinstance(candidate, expected_type):
                    candidates.append(candidate)
        return next(
            (node for node in candidates if predicate is None or predicate(node)), None
        ) or (candidates[0] if candidates else None)

    @staticmethod
    def _object(
        graph: LineageGraph,
        nodes: dict[UUID, LineageNodeBase],
        relation_type: LineageRelationType,
        subject: LineageNodeBase | None,
        expected_type: type[LineageNodeT],
        predicate: Callable[[LineageNodeT], bool] | None = None,
    ) -> LineageNodeT | None:
        if subject is None:
            return None
        candidates: list[LineageNodeT] = []
        for relation in graph.relations:
            if relation.relation is relation_type and relation.subject == subject.ref:
                candidate = nodes[relation.object.node_id]
                if isinstance(candidate, expected_type):
                    candidates.append(candidate)
        return next(
            (node for node in candidates if predicate is None or predicate(node)), None
        ) or (candidates[0] if candidates else None)
