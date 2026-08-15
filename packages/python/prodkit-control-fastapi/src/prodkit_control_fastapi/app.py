from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from prodkit_control_core import (
    ActionSpec,
    ActorKind,
    ActorRef,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequiredError,
    AuthorizationDeniedError,
    ControlEventDraft,
    DuplicateActionError,
    ControlEvent,
    EventType,
    LineageGraph,
    LineageNode,
    LineageNodeKind,
    LineageNodeRef,
    LineageRelation,
    ProductionLineageAssessment,
    RunRecord,
    RunStatus,
    sha256_hex,
)
from prodkit_control_runtime import (
    ActionBroker,
    DefaultPolicyEngine,
    DigestEffectVerifier,
    DryRunExecutor,
    ExecutorRegistry,
    InMemoryApprovalStore,
    InMemoryArtifactStore,
    InMemoryEventLedger,
    InMemoryIdempotencyStore,
    InMemoryLineageStore,
    ProductionLineagePolicy,
    RunCoordinator,
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRunRequest(APIModel):
    environment: str = "development"
    purpose: str
    actor_id: str
    actor_display_name: str | None = None
    source_intent: dict[str, object] | None = None
    specification_revision: LineageNodeRef | None = None
    workflow_id: str | None = None


class ExecuteActionRequest(APIModel):
    action: ActionSpec
    actor_id: str
    actor_display_name: str | None = None


class ApprovalRequest(APIModel):
    action: ActionSpec
    approver_id: str
    approver_role: str
    reason: str
    outcome: ApprovalOutcome = ApprovalOutcome.APPROVED
    ttl_seconds: int = Field(default=900, ge=30, le=86400)


class RecordLineageNodeRequest(APIModel):
    node: LineageNode
    actor_id: str


class RecordLineageRelationRequest(APIModel):
    relation: LineageRelation
    actor_id: str


class AssessLineageRequest(APIModel):
    observation_id: UUID
    enforce: bool = False


@dataclass
class AppServices:
    ledger: InMemoryEventLedger
    artifacts: InMemoryArtifactStore
    approvals: InMemoryApprovalStore
    policy: DefaultPolicyEngine
    coordinator: RunCoordinator
    broker: ActionBroker
    lineage: InMemoryLineageStore
    lineage_policy: ProductionLineagePolicy


def build_services() -> AppServices:
    ledger = InMemoryEventLedger()
    artifacts = InMemoryArtifactStore()
    approvals = InMemoryApprovalStore()
    policy = DefaultPolicyEngine()
    executors = ExecutorRegistry()
    executors.register(DryRunExecutor())
    broker = ActionBroker(
        ledger=ledger,
        policy=policy,
        approvals=approvals,
        idempotency=InMemoryIdempotencyStore(),
        executors=executors,
        verifier=DigestEffectVerifier(),
        artifact_store=artifacts,
    )
    return AppServices(
        ledger=ledger,
        artifacts=artifacts,
        approvals=approvals,
        policy=policy,
        coordinator=RunCoordinator(ledger),
        broker=broker,
        lineage=InMemoryLineageStore(),
        lineage_policy=ProductionLineagePolicy(),
    )


def create_app(services: AppServices | None = None) -> FastAPI:
    state = services or build_services()
    app = FastAPI(
        title="ProdKit Control",
        version="0.1.0",
        description=(
            "Provider-neutral intent-to-production lineage, action control, verification, "
            "reconciliation, and evidence API."
        ),
    )
    app.state.services = state

    def get_services() -> AppServices:
        return cast(AppServices, app.state.services)

    def require_tenant(x_prodkit_tenant_id: Annotated[str, Header(min_length=1)]) -> str:
        return x_prodkit_tenant_id

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/v1/runs", response_model=RunRecord, status_code=status.HTTP_201_CREATED)
    async def start_run(
        request: StartRunRequest,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> RunRecord:
        actor = ActorRef(
            kind=ActorKind.HUMAN,
            id=request.actor_id,
            display_name=request.actor_display_name,
            tenant_id=tenant_id,
        )
        return await services.coordinator.start_run(
            tenant_id=tenant_id,
            initiated_by=actor,
            environment=request.environment,
            purpose=request.purpose,
            source_intent=request.source_intent,
            specification_revision=request.specification_revision,
            workflow_id=request.workflow_id,
        )

    @app.get("/v1/runs/{run_id}", response_model=RunRecord)
    async def get_run(
        run_id: UUID,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> RunRecord:
        try:
            run = services.coordinator.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        if run.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/v1/runs/{run_id}/events", response_model=list[ControlEvent])
    async def list_events(
        run_id: UUID,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> list[ControlEvent]:
        try:
            run = services.coordinator.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        if run.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="run not found")
        return await services.ledger.list_run_events(run_id)

    @app.post("/v1/runs/{run_id}/actions:execute")
    async def execute_action(
        run_id: UUID,
        request: ExecuteActionRequest,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> dict[str, object]:
        action = request.action
        if action.run_id != run_id or action.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="action scope does not match request scope")
        try:
            run = services.coordinator.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        actor = ActorRef(
            kind=ActorKind.AGENT,
            id=request.actor_id,
            display_name=request.actor_display_name,
            tenant_id=tenant_id,
        )
        try:
            outcome = await services.broker.execute(action, actor=actor, trace_id=run.trace_id)
        except ApprovalRequiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "approval_required",
                    "action_id": exc.action_id,
                    "action_digest": exc.action_digest,
                },
            ) from exc
        except (AuthorizationDeniedError, DuplicateActionError) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return {
            "action": action.model_dump(mode="json"),
            "result": outcome.result.model_dump(mode="json"),
            "observation": outcome.observation.model_dump(mode="json"),
            "verification": outcome.verification.model_dump(mode="json"),
            "reused_idempotent_result": outcome.reused_idempotent_result,
        }

    @app.post("/v1/runs/{run_id}/approvals", response_model=ApprovalDecision)
    async def approve_action(
        run_id: UUID,
        request: ApprovalRequest,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> ApprovalDecision:
        action = request.action
        if action.run_id != run_id or action.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="action scope does not match request scope")
        policy = await services.policy.evaluate(action)
        now = datetime.now(UTC)
        approver = ActorRef(
            kind=ActorKind.HUMAN,
            id=request.approver_id,
            tenant_id=tenant_id,
        )
        decision = ApprovalDecision(
            approval_id=uuid4(),
            action_id=action.action_id,
            action_digest=action.digest,
            target_digest=sha256_hex(action.target),
            tenant_id=tenant_id,
            environment=action.target.environment,
            policy_decision_id=policy.decision_id,
            policy_revision=policy.policy_revision,
            approver=approver,
            approver_role=request.approver_role,
            decided_at=now,
            outcome=request.outcome,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
            reason=request.reason,
        )
        await services.approvals.record(decision)
        return decision

    @app.post("/v1/runs/{run_id}:complete", response_model=RunRecord)
    async def complete_run(
        run_id: UUID,
        actor_id: str,
        final_status: RunStatus = RunStatus.SUCCEEDED,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> RunRecord:
        actor = ActorRef(kind=ActorKind.HUMAN, id=actor_id, tenant_id=tenant_id)
        try:
            return await services.coordinator.complete_run(run_id, actor=actor, status=final_status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.post(
        "/v1/runs/{run_id}/lineage/nodes",
        response_model=LineageNode,
        status_code=status.HTTP_201_CREATED,
    )
    async def record_lineage_node(
        run_id: UUID,
        request: RecordLineageNodeRequest,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> LineageNode:
        run = _scoped_run(services, run_id, tenant_id)
        node = request.node
        if node.run_id != run_id or node.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="lineage node scope does not match run")
        await services.lineage.record_node(node)
        graph = await services.lineage.get_graph(run_id)
        services.coordinator.bind_lineage(
            run_id,
            lineage_graph_digest=sha256_hex(graph),
            specification_revision=(
                node.ref if node.kind is LineageNodeKind.SPECIFICATION_REVISION else None
            ),
        )
        now = datetime.now(UTC)
        await services.ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=EventType.LINEAGE_NODE_RECORDED,
                occurred_at=now,
                recorded_at=now,
                actor=ActorRef(kind=ActorKind.SERVICE, id=request.actor_id, tenant_id=tenant_id),
                trace_id=run.trace_id,
                span_id=secrets.token_hex(8),
                lineage=(node.ref,),
                payload={"node": node.model_dump(mode="json")},
            )
        )
        return node

    @app.post(
        "/v1/runs/{run_id}/lineage/relations",
        response_model=LineageRelation,
        status_code=status.HTTP_201_CREATED,
    )
    async def record_lineage_relation(
        run_id: UUID,
        request: RecordLineageRelationRequest,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> LineageRelation:
        run = _scoped_run(services, run_id, tenant_id)
        try:
            await services.lineage.record_relation(run_id, request.relation)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        graph = await services.lineage.get_graph(run_id)
        services.coordinator.bind_lineage(
            run_id,
            lineage_graph_digest=sha256_hex(graph),
        )
        now = datetime.now(UTC)
        await services.ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=EventType.LINEAGE_RELATION_RECORDED,
                occurred_at=now,
                recorded_at=now,
                actor=ActorRef(kind=ActorKind.SERVICE, id=request.actor_id, tenant_id=tenant_id),
                trace_id=run.trace_id,
                span_id=secrets.token_hex(8),
                lineage=(request.relation.subject, request.relation.object),
                payload={"relation": request.relation.model_dump(mode="json")},
            )
        )
        return request.relation

    @app.get("/v1/runs/{run_id}/lineage", response_model=LineageGraph)
    async def get_lineage(
        run_id: UUID,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> LineageGraph:
        _scoped_run(services, run_id, tenant_id)
        try:
            return await services.lineage.get_graph(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="lineage graph not found") from exc

    @app.post(
        "/v1/runs/{run_id}/lineage:assess",
        response_model=ProductionLineageAssessment,
    )
    async def assess_lineage(
        run_id: UUID,
        request: AssessLineageRequest,
        services: AppServices = Depends(get_services),
        tenant_id: str = Depends(require_tenant),
    ) -> ProductionLineageAssessment:
        run = _scoped_run(services, run_id, tenant_id)
        try:
            graph = await services.lineage.get_graph(run_id)
            assessment = services.lineage_policy.assess(graph, request.observation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="lineage graph not found") from exc
        now = datetime.now(UTC)
        await services.ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=EventType.LINEAGE_ASSESSED,
                occurred_at=now,
                recorded_at=now,
                actor=ActorRef(kind=ActorKind.SERVICE, id="lineage-policy", tenant_id=tenant_id),
                trace_id=run.trace_id,
                span_id=secrets.token_hex(8),
                lineage=assessment.lineage_path,
                payload={"assessment": assessment.model_dump(mode="json")},
            )
        )
        if request.enforce and not assessment.complete:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "incomplete_production_lineage",
                    "missing_requirements": [
                        requirement.value for requirement in assessment.missing_requirements
                    ],
                },
            )
        return assessment

    def _scoped_run(services: AppServices, run_id: UUID, tenant_id: str) -> RunRecord:
        try:
            run = services.coordinator.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        if run.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    return app
