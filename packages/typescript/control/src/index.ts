export type RiskClass = "low" | "medium" | "high" | "critical";
export type EffectClass = "read" | "write" | "destructive" | "privileged";
export type PolicyOutcome = "allow" | "deny" | "require_approval";
export type VerificationOutcome = "passed" | "failed" | "inconclusive";
export type ReconciliationOutcome =
  | "matched"
  | "missing_external_evidence"
  | "unexpected_external_action"
  | "state_mismatch"
  | "unverifiable";

export type LineageNodeKind =
  | "specification_revision"
  | "decision_set"
  | "generator_configuration"
  | "source_tree"
  | "verification"
  | "build_artifact"
  | "authorization"
  | "agent_action"
  | "deployment"
  | "production_observation"
  | "reconciliation";

export type LineageRelationType =
  | "generated_from"
  | "produced"
  | "verified_by"
  | "built_as"
  | "authorized_by"
  | "authorized_action"
  | "deployed_as"
  | "observed_as"
  | "compared_by";

export interface LineageNodeRef {
  readonly kind: LineageNodeKind;
  readonly node_id: string;
  readonly digest: string;
}

export interface LineageNode {
  readonly kind: LineageNodeKind;
  readonly node_id: string;
  readonly run_id: string;
  readonly tenant_id: string;
  readonly digest: string;
  readonly recorded_at: string;
  readonly external_uri?: string | null;
  readonly attributes: Readonly<Record<string, string | number | boolean | null>>;
}

export interface LineageRelation {
  readonly relation: LineageRelationType;
  readonly subject: LineageNodeRef;
  readonly object: LineageNodeRef;
  readonly recorded_at: string;
}

export interface LineageGraph {
  readonly schema_name: "prodkit.lineage-graph";
  readonly schema_version: "1.0.0";
  readonly run_id: string;
  readonly tenant_id: string;
  readonly nodes: readonly LineageNode[];
  readonly relations: readonly LineageRelation[];
}

export interface ActionTarget {
  readonly system: string;
  readonly environment: string;
  readonly resource_type: string;
  readonly resource_id: string;
  readonly region?: string | null;
  readonly expected_pre_state_digest?: string | null;
}

export interface ActionSpec {
  readonly schema_name: "prodkit.action-spec";
  readonly schema_version: "1.0.0";
  readonly action_id: string;
  readonly run_id: string;
  readonly tenant_id: string;
  readonly executor: string;
  readonly operation: string;
  readonly effect_class: EffectClass;
  readonly risk_class: RiskClass;
  readonly target: ActionTarget;
  readonly arguments: Readonly<Record<string, unknown>>;
  readonly idempotency_key: string;
  readonly proposed_at: string;
  readonly expires_at?: string | null;
  readonly expected_effect: Readonly<Record<string, unknown>>;
}

export interface ControlEvent {
  readonly schema_name: "prodkit.control-event";
  readonly schema_version: "1.0.0";
  readonly event_id: string;
  readonly run_id: string;
  readonly tenant_id: string;
  readonly sequence: number;
  readonly event_type: string;
  readonly action_id?: string | null;
  readonly lineage: readonly LineageNodeRef[];
  readonly payload: Readonly<Record<string, unknown>>;
  readonly integrity: {
    readonly previous_event_hash?: string | null;
    readonly event_hash: string;
  };
}
