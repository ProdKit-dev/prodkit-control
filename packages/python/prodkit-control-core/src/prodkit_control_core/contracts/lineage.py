from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .artifacts import ArtifactRef
from .base import ContractModel, NonBlankStr, Sha256
from .verification import ReconciliationOutcome, VerificationOutcome


class LineageNodeKind(StrEnum):
    SPECIFICATION_REVISION = "specification_revision"
    DECISION_SET = "decision_set"
    GENERATOR_CONFIGURATION = "generator_configuration"
    SOURCE_TREE = "source_tree"
    VERIFICATION = "verification"
    BUILD_ARTIFACT = "build_artifact"
    AUTHORIZATION = "authorization"
    AGENT_ACTION = "agent_action"
    DEPLOYMENT = "deployment"
    PRODUCTION_OBSERVATION = "production_observation"
    RECONCILIATION = "reconciliation"


class LineageRelationType(StrEnum):
    GENERATED_FROM = "generated_from"
    PRODUCED = "produced"
    VERIFIED_BY = "verified_by"
    BUILT_AS = "built_as"
    AUTHORIZED_BY = "authorized_by"
    AUTHORIZED_ACTION = "authorized_action"
    DEPLOYED_AS = "deployed_as"
    OBSERVED_AS = "observed_as"
    COMPARED_BY = "compared_by"


class LineageNodeRef(ContractModel):
    kind: LineageNodeKind
    node_id: UUID
    digest: Sha256


class LineageNodeBase(ContractModel):
    kind: LineageNodeKind
    node_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    digest: Sha256
    recorded_at: AwareDatetime
    external_uri: NonBlankStr | None = None
    evidence: tuple[ArtifactRef, ...] = ()
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @property
    def ref(self) -> LineageNodeRef:
        return LineageNodeRef(kind=self.kind, node_id=self.node_id, digest=self.digest)


class SpecificationRevisionNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.SPECIFICATION_REVISION] = LineageNodeKind.SPECIFICATION_REVISION
    specification_id: NonBlankStr
    revision: NonBlankStr
    constraints_digest: Sha256


class DecisionSetNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.DECISION_SET] = LineageNodeKind.DECISION_SET
    decision_set_id: NonBlankStr
    revision: NonBlankStr


class GeneratorConfigurationNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.GENERATOR_CONFIGURATION] = LineageNodeKind.GENERATOR_CONFIGURATION
    generator_name: NonBlankStr
    generator_version: NonBlankStr
    input_digest: Sha256
    provider_name: NonBlankStr | None = None
    model_name: NonBlankStr | None = None
    succeeded: bool


class SourceTreeNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.SOURCE_TREE] = LineageNodeKind.SOURCE_TREE
    repository: NonBlankStr | None = None
    revision: NonBlankStr | None = None


class VerificationNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.VERIFICATION] = LineageNodeKind.VERIFICATION
    verifier_name: NonBlankStr
    verifier_version: NonBlankStr
    requirements_digest: Sha256
    results_digest: Sha256
    outcome: VerificationOutcome


class BuildArtifactNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.BUILD_ARTIFACT] = LineageNodeKind.BUILD_ARTIFACT
    build_id: NonBlankStr
    builder_identity: NonBlankStr
    succeeded: bool


class AuthorizationNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.AUTHORIZATION] = LineageNodeKind.AUTHORIZATION
    policy_digest: Sha256
    action_set_digest: Sha256
    approval_digest: Sha256 | None = None
    authorized: bool


class AgentActionNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.AGENT_ACTION] = LineageNodeKind.AGENT_ACTION
    action_id: UUID
    action_digest: Sha256
    executor_identity: NonBlankStr
    succeeded: bool


class DeploymentNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.DEPLOYMENT] = LineageNodeKind.DEPLOYMENT
    deployment_id: NonBlankStr
    environment: NonBlankStr
    target: NonBlankStr
    deployed_at: AwareDatetime
    succeeded: bool


class ProductionObservationNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.PRODUCTION_OBSERVATION] = LineageNodeKind.PRODUCTION_OBSERVATION
    observation_id: UUID
    environment: NonBlankStr
    observer_identity: NonBlankStr
    observed_at: AwareDatetime


class ReconciliationNode(LineageNodeBase):
    kind: Literal[LineageNodeKind.RECONCILIATION] = LineageNodeKind.RECONCILIATION
    reconciliation_id: UUID
    reconciler_name: NonBlankStr
    findings_digest: Sha256
    outcome: ReconciliationOutcome


LineageNode = Annotated[
    SpecificationRevisionNode
    | DecisionSetNode
    | GeneratorConfigurationNode
    | SourceTreeNode
    | VerificationNode
    | BuildArtifactNode
    | AuthorizationNode
    | AgentActionNode
    | DeploymentNode
    | ProductionObservationNode
    | ReconciliationNode,
    Field(discriminator="kind"),
]


class LineageRelation(ContractModel):
    relation: LineageRelationType
    subject: LineageNodeRef
    object: LineageNodeRef
    recorded_at: AwareDatetime
    evidence: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def validate_not_self_referential(self) -> LineageRelation:
        if self.subject == self.object:
            raise ValueError("a lineage relation cannot refer to itself")
        return self


_ALLOWED_RELATIONS: dict[LineageRelationType, set[tuple[LineageNodeKind, LineageNodeKind]]] = {
    LineageRelationType.GENERATED_FROM: {
        (LineageNodeKind.GENERATOR_CONFIGURATION, LineageNodeKind.SPECIFICATION_REVISION),
        (LineageNodeKind.GENERATOR_CONFIGURATION, LineageNodeKind.DECISION_SET),
    },
    LineageRelationType.PRODUCED: {
        (LineageNodeKind.GENERATOR_CONFIGURATION, LineageNodeKind.SOURCE_TREE),
    },
    LineageRelationType.VERIFIED_BY: {
        (LineageNodeKind.SOURCE_TREE, LineageNodeKind.VERIFICATION),
    },
    LineageRelationType.BUILT_AS: {
        (LineageNodeKind.SOURCE_TREE, LineageNodeKind.BUILD_ARTIFACT),
    },
    LineageRelationType.AUTHORIZED_BY: {
        (LineageNodeKind.BUILD_ARTIFACT, LineageNodeKind.AUTHORIZATION),
    },
    LineageRelationType.AUTHORIZED_ACTION: {
        (LineageNodeKind.AUTHORIZATION, LineageNodeKind.AGENT_ACTION),
    },
    LineageRelationType.DEPLOYED_AS: {
        (LineageNodeKind.AGENT_ACTION, LineageNodeKind.DEPLOYMENT),
    },
    LineageRelationType.OBSERVED_AS: {
        (LineageNodeKind.DEPLOYMENT, LineageNodeKind.PRODUCTION_OBSERVATION),
    },
    LineageRelationType.COMPARED_BY: {
        (LineageNodeKind.PRODUCTION_OBSERVATION, LineageNodeKind.RECONCILIATION),
    },
}


class LineageGraph(ContractModel):
    schema_name: str = "prodkit.lineage-graph"
    schema_version: str = "1.0.0"
    run_id: UUID
    tenant_id: NonBlankStr
    nodes: tuple[LineageNode, ...] = ()
    relations: tuple[LineageRelation, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> LineageGraph:
        nodes_by_id: dict[UUID, LineageNodeBase] = {}
        for node in self.nodes:
            if node.run_id != self.run_id or node.tenant_id != self.tenant_id:
                raise ValueError("every lineage node must match the graph run and tenant")
            if node.node_id in nodes_by_id:
                raise ValueError(f"duplicate lineage node id: {node.node_id}")
            nodes_by_id[node.node_id] = node

        relation_keys: set[tuple[LineageRelationType, UUID, UUID]] = set()
        adjacency: dict[UUID, list[UUID]] = {node_id: [] for node_id in nodes_by_id}
        for relation in self.relations:
            subject = nodes_by_id.get(relation.subject.node_id)
            object_node = nodes_by_id.get(relation.object.node_id)
            if subject is None or object_node is None:
                raise ValueError("every lineage relation endpoint must exist in the graph")
            if subject.ref != relation.subject or object_node.ref != relation.object:
                raise ValueError("lineage relation endpoint digest or kind does not match its node")
            pair = (subject.kind, object_node.kind)
            if pair not in _ALLOWED_RELATIONS[relation.relation]:
                raise ValueError(
                    f"{relation.relation.value} cannot link "
                    f"{subject.kind.value} to {object_node.kind.value}"
                )
            key = (relation.relation, subject.node_id, object_node.node_id)
            if key in relation_keys:
                raise ValueError("duplicate lineage relation")
            relation_keys.add(key)
            adjacency[subject.node_id].append(object_node.node_id)

        self._validate_acyclic(adjacency)
        return self

    @staticmethod
    def _validate_acyclic(adjacency: dict[UUID, list[UUID]]) -> None:
        visiting: set[UUID] = set()
        visited: set[UUID] = set()

        def visit(node_id: UUID) -> None:
            if node_id in visiting:
                raise ValueError("lineage graph must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target_id in adjacency[node_id]:
                visit(target_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in adjacency:
            visit(node_id)


class ProductionLineageRequirement(StrEnum):
    SPECIFICATION_REVISION = "specification_revision"
    DECISION_SET = "decision_set"
    SUCCESSFUL_GENERATION = "successful_generation"
    SOURCE_TREE = "source_tree"
    PASSING_VERIFICATION = "passing_verification"
    SUCCESSFUL_BUILD = "successful_build"
    AUTHORIZATION = "authorization"
    SUCCESSFUL_AGENT_ACTION = "successful_agent_action"
    SUCCESSFUL_DEPLOYMENT = "successful_deployment"
    PRODUCTION_OBSERVATION = "production_observation"
    MATCHED_RECONCILIATION = "matched_reconciliation"


class ProductionLineageAssessment(ContractModel):
    schema_name: str = "prodkit.production-lineage-assessment"
    schema_version: str = "1.0.0"
    observation: LineageNodeRef
    complete: bool
    satisfied_requirements: tuple[ProductionLineageRequirement, ...]
    missing_requirements: tuple[ProductionLineageRequirement, ...]
    lineage_path: tuple[LineageNodeRef, ...]
