from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import typer

from prodkit_control_core import (
    ActionSpec,
    ActionTarget,
    AgentActionNode,
    ActorKind,
    ActorRef,
    AuthorizationNode,
    BuildArtifactNode,
    DecisionSetNode,
    DeploymentNode,
    EffectClass,
    GeneratorConfigurationNode,
    LineageGraph,
    LineageNodeBase,
    LineageRelation,
    LineageRelationType,
    ProductionObservationNode,
    ReconciliationNode,
    ReconciliationOutcome,
    RiskClass,
    RunRecord,
    RunStatus,
    SourceTreeNode,
    SpecificationRevisionNode,
    VerificationNode,
    sha256_hex,
)
from prodkit_control_runtime import (
    ActionBroker,
    BrokerOutcome,
    DefaultPolicyEngine,
    DigestEffectVerifier,
    DryRunExecutor,
    EvidenceBundleBuilder,
    EvidenceBundleVerifier,
    ExecutorRegistry,
    InMemoryApprovalStore,
    InMemoryArtifactStore,
    InMemoryEventLedger,
    InMemoryIdempotencyStore,
    ProductionLineagePolicy,
    RunCoordinator,
)

app = typer.Typer(help="ProdKit Control command-line utilities.", no_args_is_help=True)


@app.command()
def demo(output: Path = Path("artifacts/demo-run")) -> None:
    """Run a deterministic end-to-end dry-run and export tenant-bound evidence."""

    archive = asyncio.run(_demo(output))
    typer.echo(f"Evidence bundle: {archive}")
    manifest = EvidenceBundleVerifier().verify(archive)
    typer.echo(
        f"Verified {manifest['event_count']} events; final hash {manifest['final_event_hash']}"
    )
    typer.echo(
        f"Verified lineage: {manifest['lineage_node_count']} nodes, "
        f"{manifest['lineage_relation_count']} relations"
    )


async def _demo(output: Path) -> Path:
    tenant = "demo-tenant"
    actor = ActorRef(kind=ActorKind.HUMAN, id="demo-user", tenant_id=tenant)
    ledger = InMemoryEventLedger()
    approvals = InMemoryApprovalStore()
    executors = ExecutorRegistry()
    executors.register(DryRunExecutor())
    coordinator = RunCoordinator(ledger)
    broker = ActionBroker(
        ledger=ledger,
        policy=DefaultPolicyEngine(),
        approvals=approvals,
        idempotency=InMemoryIdempotencyStore(),
        executors=executors,
        verifier=DigestEffectVerifier(),
        artifact_store=InMemoryArtifactStore(output / "content"),
    )
    run = await coordinator.start_run(
        tenant_id=tenant,
        initiated_by=actor,
        environment="development",
        purpose="Demonstrate provider-independent execution evidence",
        source_intent={"capability": "CAP-DEMO", "acceptance_criterion": "AC-DEMO"},
    )
    action = ActionSpec(
        action_id=uuid4(),
        run_id=run.run_id,
        tenant_id=tenant,
        executor="dry-run",
        operation="repository.preview_change",
        effect_class=EffectClass.READ,
        risk_class=RiskClass.LOW,
        target=ActionTarget(
            system="git",
            environment="development",
            resource_type="repository",
            resource_id="prodkit/demo",
        ),
        arguments={"path": "README.md", "operation": "preview"},
        idempotency_key=f"demo-{run.run_id}",
        proposed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        work_pack_id="WORK-PACK-DEMO",
        expected_effect={"changed": False, "previewed": True},
    )
    agent = ActorRef(kind=ActorKind.AGENT, id="demo-agent", tenant_id=tenant)
    outcome = await broker.execute(action, actor=agent, trace_id=run.trace_id)
    lineage = _build_demo_lineage(run, action, outcome)
    ProductionLineagePolicy().enforce(
        lineage,
        next(node.node_id for node in lineage.nodes if isinstance(node, ProductionObservationNode)),
    )
    specification = next(
        node for node in lineage.nodes if isinstance(node, SpecificationRevisionNode)
    )
    await coordinator.bind_lineage(
        run.run_id,
        tenant_id=tenant,
        lineage_graph_digest=sha256_hex(lineage),
        specification_revision=specification.ref,
    )
    await coordinator.complete_run(
        run.run_id,
        actor=actor,
        status=RunStatus.SUCCEEDED,
        summary={
            "verification": outcome.verification.outcome.value,
            "production_lineage": "complete",
        },
    )
    return await EvidenceBundleBuilder(ledger).build(
        run.run_id,
        output / str(run.run_id),
        tenant_id=tenant,
        lineage=lineage,
    )


def _build_demo_lineage(
    run: RunRecord,
    action: ActionSpec,
    outcome: BrokerOutcome,
) -> LineageGraph:
    now = datetime.now(UTC)
    specification = SpecificationRevisionNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=run.source_intent_digest or sha256_hex("demo-intent"),
        specification_id="CAP-DEMO",
        revision="1",
        constraints_digest=sha256_hex("demo-constraints"),
    )
    decisions = DecisionSetNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex("demo-decisions"),
        decision_set_id="DECISIONS-DEMO",
        revision="1",
    )
    generator = GeneratorConfigurationNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex("demo-generator-configuration"),
        generator_name="prodkit-demo-generator",
        generator_version="1.0.0",
        input_digest=specification.digest,
        succeeded=True,
    )
    source = SourceTreeNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex({"generated_from": generator.digest}),
        repository="prodkit/demo",
        revision="demo-source-v1",
    )
    verification = VerificationNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex(outcome.verification),
        verifier_name=outcome.verification.verifier,
        verifier_version=outcome.verification.verifier_version,
        requirements_digest=sha256_hex(action.expected_effect),
        results_digest=sha256_hex(outcome.verification.details),
        outcome=outcome.verification.outcome,
    )
    build = BuildArtifactNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex({"source_tree": source.digest, "build": "demo"}),
        build_id="demo-build-1",
        builder_identity="prodkit-demo-builder",
        succeeded=True,
    )
    authorization = AuthorizationNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex({"action": action.digest, "policy": "default-policy-v1"}),
        policy_digest=sha256_hex("default-policy-v1"),
        action_set_digest=action.digest,
        authorized=True,
    )
    agent_action = AgentActionNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex(outcome.result),
        action_id=action.action_id,
        action_digest=action.digest,
        executor_identity=outcome.result.executor_identity,
        succeeded=outcome.result.succeeded,
    )
    deployment = DeploymentNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex({"artifact": build.digest, "target": action.target}),
        deployment_id="demo-deployment-1",
        environment=action.target.environment,
        target=f"{action.target.system}/{action.target.resource_id}",
        deployed_at=outcome.result.completed_at,
        succeeded=outcome.result.succeeded,
    )
    observation = ProductionObservationNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=outcome.observation.state_digest,
        observation_id=outcome.observation.observation_id,
        environment=action.target.environment,
        observer_identity=outcome.verification.verifier,
        observed_at=outcome.observation.observed_at,
    )
    reconciliation = ReconciliationNode(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        recorded_at=now,
        node_id=uuid4(),
        digest=sha256_hex({"observation": observation.digest, "outcome": "matched"}),
        reconciliation_id=uuid4(),
        reconciler_name="prodkit-demo-reconciler",
        findings_digest=sha256_hex("no-findings"),
        outcome=ReconciliationOutcome.MATCHED,
    )
    nodes = (
        specification,
        decisions,
        generator,
        source,
        verification,
        build,
        authorization,
        agent_action,
        deployment,
        observation,
        reconciliation,
    )

    def relates(
        relation: LineageRelationType,
        subject: LineageNodeBase,
        object_node: LineageNodeBase,
    ) -> LineageRelation:
        return LineageRelation(
            relation=relation,
            subject=subject.ref,
            object=object_node.ref,
            recorded_at=now,
        )

    relations = (
        relates(LineageRelationType.GENERATED_FROM, generator, specification),
        relates(LineageRelationType.GENERATED_FROM, generator, decisions),
        relates(LineageRelationType.PRODUCED, generator, source),
        relates(LineageRelationType.VERIFIED_BY, source, verification),
        relates(LineageRelationType.BUILT_AS, source, build),
        relates(LineageRelationType.AUTHORIZED_BY, build, authorization),
        relates(LineageRelationType.AUTHORIZED_ACTION, authorization, agent_action),
        relates(LineageRelationType.DEPLOYED_AS, agent_action, deployment),
        relates(LineageRelationType.OBSERVED_AS, deployment, observation),
        relates(LineageRelationType.COMPARED_BY, observation, reconciliation),
    )
    return LineageGraph(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        nodes=nodes,
        relations=relations,
    )


@app.command("verify-bundle")
def verify_bundle(path: Path) -> None:
    """Verify event ordering, hash chaining, tenant scope, and manifest integrity."""

    manifest = EvidenceBundleVerifier().verify(path)
    typer.echo(
        f"OK: tenant={manifest['tenant_id']} run={manifest['run_id']} "
        f"events={manifest['event_count']}"
    )


if __name__ == "__main__":
    app()
