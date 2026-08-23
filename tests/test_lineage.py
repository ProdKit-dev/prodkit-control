from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

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
    LineageNodeBase,
    LineageRelation,
    LineageRelationType,
    ProductionLineageRequirement,
    ProductionObservationNode,
    ReconciliationNode,
    ReconciliationOutcome,
    SourceTreeNode,
    SpecificationRevisionNode,
    VerificationNode,
    VerificationOutcome,
    sha256_hex,
)
from prodkit_control_runtime import InMemoryLineageStore, ProductionLineagePolicy


def _digest(value: str) -> str:
    return sha256_hex(value)


def make_complete_lineage(
    *,
    run_id: UUID | None = None,
    tenant_id: str = "tenant-test",
) -> tuple[LineageGraph, ProductionObservationNode]:
    run = run_id or uuid4()
    now = datetime.now(UTC)
    common = {"run_id": run, "tenant_id": tenant_id, "recorded_at": now}
    specification = SpecificationRevisionNode(
        **common,
        node_id=uuid4(),
        digest=_digest("specification"),
        specification_id="SPEC-1",
        revision="7",
        constraints_digest=_digest("constraints"),
    )
    decisions = DecisionSetNode(
        **common,
        node_id=uuid4(),
        digest=_digest("decisions"),
        decision_set_id="DECISIONS-1",
        revision="3",
    )
    generator = GeneratorConfigurationNode(
        **common,
        node_id=uuid4(),
        digest=_digest("generator-configuration"),
        generator_name="prodkit-generator",
        generator_version="2.1.0",
        input_digest=_digest("generator-inputs"),
        provider_name="provider",
        model_name="model",
        succeeded=True,
    )
    source_tree = SourceTreeNode(
        **common,
        node_id=uuid4(),
        digest=_digest("source-tree"),
        repository="https://example.test/repository.git",
        revision="abc123",
    )
    verification = VerificationNode(
        **common,
        node_id=uuid4(),
        digest=_digest("verification"),
        verifier_name="ci",
        verifier_version="1.0.0",
        requirements_digest=_digest("verification-requirements"),
        results_digest=_digest("verification-results"),
        outcome=VerificationOutcome.PASSED,
    )
    build = BuildArtifactNode(
        **common,
        node_id=uuid4(),
        digest=_digest("artifact"),
        build_id="build-42",
        builder_identity="builder@example.test",
        succeeded=True,
    )
    authorization = AuthorizationNode(
        **common,
        node_id=uuid4(),
        digest=_digest("authorization"),
        policy_digest=_digest("policy"),
        approval_digest=_digest("approval"),
        action_set_digest=_digest("action-set"),
        authorized=True,
    )
    action = AgentActionNode(
        **common,
        node_id=uuid4(),
        digest=_digest("agent-action-record"),
        action_id=uuid4(),
        action_digest=_digest("agent-action"),
        executor_identity="deployment-executor@example.test",
        succeeded=True,
    )
    deployment = DeploymentNode(
        **common,
        node_id=uuid4(),
        digest=_digest("deployment"),
        deployment_id="deployment-9",
        environment="production",
        target="cluster-a/namespace-a/service-a",
        deployed_at=now,
        succeeded=True,
    )
    observation = ProductionObservationNode(
        **common,
        node_id=uuid4(),
        digest=_digest("production-state"),
        observation_id=uuid4(),
        environment="production",
        observer_identity="observer@example.test",
        observed_at=now,
    )
    reconciliation = ReconciliationNode(
        **common,
        node_id=uuid4(),
        digest=_digest("reconciliation"),
        reconciliation_id=uuid4(),
        reconciler_name="independent-auditor",
        findings_digest=_digest("findings"),
        outcome=ReconciliationOutcome.MATCHED,
    )
    nodes = (
        specification,
        decisions,
        generator,
        source_tree,
        verification,
        build,
        authorization,
        action,
        deployment,
        observation,
        reconciliation,
    )

    def relation(
        relation_type: LineageRelationType,
        subject: LineageNodeBase,
        object_node: LineageNodeBase,
    ) -> LineageRelation:
        return LineageRelation(
            relation=relation_type,
            subject=subject.ref,
            object=object_node.ref,
            recorded_at=now,
        )

    relations = (
        relation(LineageRelationType.GENERATED_FROM, generator, specification),
        relation(LineageRelationType.GENERATED_FROM, generator, decisions),
        relation(LineageRelationType.PRODUCED, generator, source_tree),
        relation(LineageRelationType.VERIFIED_BY, source_tree, verification),
        relation(LineageRelationType.BUILT_AS, source_tree, build),
        relation(LineageRelationType.AUTHORIZED_BY, build, authorization),
        relation(LineageRelationType.AUTHORIZED_ACTION, authorization, action),
        relation(LineageRelationType.DEPLOYED_AS, action, deployment),
        relation(LineageRelationType.OBSERVED_AS, deployment, observation),
        relation(LineageRelationType.COMPARED_BY, observation, reconciliation),
    )
    return (
        LineageGraph(run_id=run, tenant_id=tenant_id, nodes=nodes, relations=relations),
        observation,
    )


def test_complete_production_lineage_is_accepted() -> None:
    graph, observation = make_complete_lineage()

    assessment = ProductionLineagePolicy().enforce(graph, observation.ref)

    assert assessment.complete is True
    assert assessment.missing_requirements == ()
    assert set(assessment.satisfied_requirements) == set(ProductionLineageRequirement)
    assert assessment.lineage_path[0].kind.value == "specification_revision"
    assert assessment.lineage_path[-1].kind.value == "reconciliation"


@pytest.mark.parametrize(
    ("node_type", "updates", "missing"),
    [
        (GeneratorConfigurationNode, {"succeeded": False}, "successful_generation"),
        (VerificationNode, {"outcome": VerificationOutcome.FAILED}, "passing_verification"),
        (BuildArtifactNode, {"succeeded": False}, "successful_build"),
        (AuthorizationNode, {"authorized": False}, "authorization"),
        (AgentActionNode, {"succeeded": False}, "successful_agent_action"),
        (DeploymentNode, {"succeeded": False}, "successful_deployment"),
        (
            ReconciliationNode,
            {"outcome": ReconciliationOutcome.STATE_MISMATCH},
            "matched_reconciliation",
        ),
    ],
)
def test_failed_stage_is_rejected(node_type, updates, missing: str) -> None:
    graph, observation = make_complete_lineage()
    nodes = tuple(
        node.model_copy(update=updates) if isinstance(node, node_type) else node
        for node in graph.nodes
    )
    changed = next(node for node in nodes if isinstance(node, node_type))
    original = next(node for node in graph.nodes if isinstance(node, node_type))
    relations = tuple(
        relation.model_copy(
            update={
                "subject": changed.ref if relation.subject == original.ref else relation.subject,
                "object": changed.ref if relation.object == original.ref else relation.object,
            }
        )
        for relation in graph.relations
    )
    modified = graph.model_copy(update={"nodes": nodes, "relations": relations})

    assessment = ProductionLineagePolicy().assess(modified, observation.node_id)

    assert assessment.complete is False
    assert missing in {requirement.value for requirement in assessment.missing_requirements}
    with pytest.raises(IncompleteLineageError, match=missing):
        ProductionLineagePolicy().enforce(modified, observation.node_id)


def test_disconnected_observation_reports_every_missing_stage() -> None:
    graph, observation = make_complete_lineage()
    disconnected = LineageGraph(
        run_id=graph.run_id,
        tenant_id=graph.tenant_id,
        nodes=(observation,),
    )

    assessment = ProductionLineagePolicy().assess(disconnected, observation.node_id)

    assert assessment.complete is False
    assert assessment.satisfied_requirements == (
        ProductionLineageRequirement.PRODUCTION_OBSERVATION,
    )
    assert len(assessment.missing_requirements) == len(ProductionLineageRequirement) - 1


def test_graph_rejects_invalid_relations_and_scope() -> None:
    graph, _ = make_complete_lineage()
    source = next(node for node in graph.nodes if isinstance(node, SourceTreeNode))
    deployment = next(node for node in graph.nodes if isinstance(node, DeploymentNode))
    invalid = LineageRelation(
        relation=LineageRelationType.BUILT_AS,
        subject=source.ref,
        object=deployment.ref,
        recorded_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError, match="cannot link"):
        LineageGraph(
            run_id=graph.run_id,
            tenant_id=graph.tenant_id,
            nodes=graph.nodes,
            relations=(invalid,),
        )

    wrong_tenant = source.model_copy(update={"tenant_id": "another-tenant"})
    with pytest.raises(ValidationError, match="must match the graph run and tenant"):
        LineageGraph(
            run_id=graph.run_id,
            tenant_id=graph.tenant_id,
            nodes=(wrong_tenant,),
        )


@pytest.mark.asyncio
async def test_lineage_store_is_idempotent_append_only_and_tenant_scoped() -> None:
    graph, _ = make_complete_lineage()
    store = InMemoryLineageStore()
    with pytest.raises(KeyError, match="does not exist"):
        await store.get_graph(tenant_id=graph.tenant_id, run_id=graph.run_id)

    for node in graph.nodes:
        await store.record_node(node)
        await store.record_node(node)
    for relation in graph.relations:
        await store.record_relation(
            tenant_id=graph.tenant_id,
            run_id=graph.run_id,
            relation=relation,
        )
        await store.record_relation(
            tenant_id=graph.tenant_id,
            run_id=graph.run_id,
            relation=relation,
        )

    stored = await store.get_graph(tenant_id=graph.tenant_id, run_id=graph.run_id)
    assert stored == graph
    with pytest.raises(KeyError, match="does not exist"):
        await store.get_graph(tenant_id="foreign-tenant", run_id=graph.run_id)

    first = graph.nodes[0]
    with pytest.raises(IntegrityViolationError, match="cannot be rewritten"):
        await store.record_node(first.model_copy(update={"digest": _digest("changed")}))
    with pytest.raises(IntegrityViolationError, match="tenant boundaries"):
        await store.record_node(
            first.model_copy(update={"node_id": uuid4(), "tenant_id": "another-tenant"})
        )
    first_relation = graph.relations[0]
    with pytest.raises(IntegrityViolationError, match="relation cannot be rewritten"):
        await store.record_relation(
            tenant_id=graph.tenant_id,
            run_id=graph.run_id,
            relation=first_relation.model_copy(
                update={"recorded_at": first_relation.recorded_at + timedelta(seconds=1)}
            ),
        )
