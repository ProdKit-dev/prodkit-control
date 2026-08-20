from __future__ import annotations

import inspect
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, TypeAlias, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prodkit_control_core import (
    ActionSpec,
    ActorKind,
    ActorRef,
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequiredError,
    AuthorizationDeniedError,
    ControlEvent,
    ControlEventDraft,
    DuplicateActionError,
    EventType,
    LineageGraph,
    LineageNode,
    LineageNodeKind,
    LineageNodeRef,
    LineageRelation,
    PolicyOutcome,
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


class RequestPrincipal(APIModel):
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_kind: ActorKind
    display_name: str | None = None
    roles: tuple[str, ...] = ()

    def actor(self) -> ActorRef:
        return ActorRef(
            kind=self.actor_kind,
            id=self.actor_id,
            display_name=self.display_name,
            tenant_id=self.tenant_id,
        )


PrincipalResolver = Callable[[Request], RequestPrincipal | Awaitable[RequestPrincipal]]


class StartRunRequest(APIModel):
    environment: str = "development"
    purpose: str
    source_intent: dict[str, object] | None = None
    specification_revision: LineageNodeRef | None = None
    workflow_id: str | None = None


class ExecuteActionRequest(APIModel):
    action: ActionSpec


class ApprovalRequest(APIModel):
    action: ActionSpec
    reason: str
    outcome: ApprovalOutcome = ApprovalOutcome.APPROVED
    ttl_seconds: int = Field(default=900, ge=30, le=86400)


class RecordLineageNodeRequest(APIModel):
    node: LineageNode


class RecordLineageRelationRequest(APIModel):
    relation: LineageRelation


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
    """Build an in-memory standalone service graph for tests and development."""
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


def _get_services(request: Request) -> AppServices:
    try:
        services = request.app.state.services
    except AttributeError as exc:  # pragma: no cover - app factory always wires services
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="control services are not configured",
        ) from exc
    return cast(AppServices, services)


async def _require_principal(request: Request) -> RequestPrincipal:
    resolver = cast(
        PrincipalResolver | None,
        getattr(request.app.state, "principal_resolver", None),
    )
    allow_insecure_header_auth = bool(
        getattr(request.app.state, "allow_insecure_header_auth", False)
    )
    if resolver is not None:
        resolved = resolver(request)
        if inspect.isawaitable(resolved):
            resolved = await cast(Awaitable[RequestPrincipal], resolved)
        if not isinstance(resolved, RequestPrincipal):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="principal resolver returned an invalid principal",
            )
        return resolved
    if not allow_insecure_header_auth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "authentication_not_configured",
                "message": "configure a principal resolver for protected API routes",
            },
        )
    return _insecure_header_principal(request)


Principal: TypeAlias = Annotated[RequestPrincipal, Depends(_require_principal)]
Services: TypeAlias = Annotated[AppServices, Depends(_get_services)]


def create_app(
    services: AppServices | None = None,
    *,
    principal_resolver: PrincipalResolver | None = None,
    allow_insecure_header_auth: bool = False,
) -> FastAPI:
    """Create the API.

    Production deployments must provide ``principal_resolver``. Header-based identity is
    intentionally disabled unless ``allow_insecure_header_auth`` is explicitly enabled.
    """
    app = FastAPI(
        title="ProdKit Control",
        version="0.0.1",
        description=(
            "Provider-neutral intent-to-production lineage, action control, verification, "
            "reconciliation, and evidence API."
        ),
    )
    app.state.services = services or build_services()
    app.state.principal_resolver = principal_resolver
    app.state.allow_insecure_header_auth = allow_insecure_header_auth

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    async def readyz() -> dict[str, str]:
        if principal_resolver is None and not allow_insecure_header_auth:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "not_ready", "reason": "authentication_not_configured"},
            )
        return {"status": "ready"}

    @app.post("/v1/runs", response_model=RunRecord, status_code=status.HTTP_201_CREATED)
    async def start_run(
        request: StartRunRequest,
        principal: Principal,
        services: Services,
    ) -> RunRecord:
        return await services.coordinator.start_run(
            tenant_id=principal.tenant_id,
            initiated_by=principal.actor(),
            environment=request.environment,
            purpose=request.purpose,
            source_intent=request.source_intent,
            specification_revision=request.specification_revision,
            workflow_id=request.workflow_id,
        )

    @app.get("/v1/runs/{run_id}", response_model=RunRecord)
    async def get_run(run_id: UUID, principal: Principal, services: Services) -> RunRecord:
        return _scoped_run(services, run_id, principal.tenant_id)

    @app.get("/v1/runs/{run_id}/events", response_model=list[ControlEvent])
    async def list_events(
        run_id: UUID,
        principal: Principal,
        services: Services,
    ) -> list[ControlEvent]:
        _scoped_run(services, run_id, principal.tenant_id)
        return await services.ledger.list_run_events(run_id)

    @app.post("/v1/runs/{run_id}/actions:execute")
    async def execute_action(
        run_id: UUID,
        request: ExecuteActionRequest,
        principal: Principal,
        services: Services,
    ) -> dict[str, object]:
        action = request.action
        if action.run_id != run_id or action.tenant_id != principal.tenant_id:
            raise HTTPException(status_code=422, detail="action scope does not match request scope")
        run = _scoped_run(services, run_id, principal.tenant_id)
        try:
            outcome = await services.broker.execute(
                action,
                actor=principal.actor(),
                trace_id=run.trace_id,
            )
        except ApprovalRequiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "approval_required",
                    "action_id": exc.action_id,
                    "action_digest": exc.action_digest,
                },
            ) from exc
        except DuplicateActionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "duplicate_action", "message": str(exc)},
            ) from exc
        except AuthorizationDeniedError as exc:
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
        principal: Principal,
        services: Services,
    ) -> ApprovalDecision:
        action = request.action
        if action.run_id != run_id or action.tenant_id != principal.tenant_id:
            raise HTTPException(status_code=422, detail="action scope does not match request scope")
        _scoped_run(services, run_id, principal.tenant_id)
        policy = await services.policy.evaluate(action)
        if policy.outcome is not PolicyOutcome.REQUIRE_APPROVAL:
            raise HTTPException(status_code=409, detail="action does not require approval")
        authorized_roles = sorted(set(principal.roles).intersection(policy.required_approval_roles))
        if not authorized_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="authenticated principal does not hold a required approval role",
            )
        now = datetime.now(UTC)
        decision = ApprovalDecision(
            approval_id=uuid4(),
            action_id=action.action_id,
            action_digest=action.digest,
            target_digest=sha256_hex(action.target),
            tenant_id=principal.tenant_id,
            environment=action.target.environment,
            policy_decision_id=policy.decision_id,
            policy_revision=policy.policy_revision,
            approver=principal.actor(),
            approver_role=authorized_roles[0],
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
        principal: Principal,
        services: Services,
        final_status: RunStatus = RunStatus.SUCCEEDED,
    ) -> RunRecord:
        _scoped_run(services, run_id, principal.tenant_id)
        try:
            return await services.coordinator.complete_run(
                run_id,
                actor=principal.actor(),
                status=final_status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/runs/{run_id}/lineage/nodes",
        response_model=LineageNode,
        status_code=status.HTTP_201_CREATED,
    )
    async def record_lineage_node(
        run_id: UUID,
        request: RecordLineageNodeRequest,
        principal: Principal,
        services: Services,
    ) -> LineageNode:
        run = _scoped_run(services, run_id, principal.tenant_id)
        node = request.node
        if node.run_id != run_id or node.tenant_id != principal.tenant_id:
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
                tenant_id=principal.tenant_id,
                event_type=EventType.LINEAGE_NODE_RECORDED,
                occurred_at=now,
                recorded_at=now,
                actor=principal.actor(),
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
        principal: Principal,
        services: Services,
    ) -> LineageRelation:
        run = _scoped_run(services, run_id, principal.tenant_id)
        try:
            await services.lineage.record_relation(run_id, request.relation)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        graph = await services.lineage.get_graph(run_id)
        services.coordinator.bind_lineage(run_id, lineage_graph_digest=sha256_hex(graph))
        now = datetime.now(UTC)
        await services.ledger.append(
            ControlEventDraft(
                event_id=uuid4(),
                run_id=run_id,
                tenant_id=principal.tenant_id,
                event_type=EventType.LINEAGE_RELATION_RECORDED,
                occurred_at=now,
                recorded_at=now,
                actor=principal.actor(),
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
        principal: Principal,
        services: Services,
    ) -> LineageGraph:
        _scoped_run(services, run_id, principal.tenant_id)
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
        principal: Principal,
        services: Services,
    ) -> ProductionLineageAssessment:
        run = _scoped_run(services, run_id, principal.tenant_id)
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
                tenant_id=principal.tenant_id,
                event_type=EventType.LINEAGE_ASSESSED,
                occurred_at=now,
                recorded_at=now,
                actor=principal.actor(),
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

    return app


def _insecure_header_principal(request: Request) -> RequestPrincipal:
    tenant_id = request.headers.get("x-prodkit-tenant-id", "").strip()
    actor_id = request.headers.get("x-prodkit-actor-id", "").strip()
    actor_kind_raw = request.headers.get("x-prodkit-actor-kind", "").strip()
    if not tenant_id or not actor_id or not actor_kind_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="development header authentication requires tenant, actor id, and actor kind",
        )
    try:
        actor_kind = ActorKind(actor_kind_raw)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid actor kind") from exc
    roles = tuple(
        role.strip()
        for role in request.headers.get("x-prodkit-roles", "").split(",")
        if role.strip()
    )
    return RequestPrincipal(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_kind=actor_kind,
        display_name=request.headers.get("x-prodkit-actor-display-name"),
        roles=roles,
    )


def _scoped_run(services: AppServices, run_id: UUID, tenant_id: str) -> RunRecord:
    try:
        run = services.coordinator.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if run.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="run not found")
    return run
