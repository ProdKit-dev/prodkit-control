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

export type ActorKind = "human" | "agent" | "service" | "workflow" | "executor";

export interface ActorRef {
  readonly kind: ActorKind;
  readonly id: string;
  readonly display_name?: string | null;
  readonly tenant_id: string;
  readonly workload_identity?: string | null;
  readonly attributes: Readonly<Record<string, string>>;
}

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

export type WorkState = "queued" | "leased" | "succeeded" | "dead_letter";

export interface FencedLease {
  readonly schema_name: "prodkit.fenced-lease";
  readonly schema_version: "1.0.0";
  readonly lease_id: string;
  readonly tenant_id: string;
  readonly resource_key: string;
  readonly owner_id: string;
  readonly fence_token: number;
  readonly acquired_at: string;
  readonly expires_at: string;
}

export interface DurableWorkItem {
  readonly schema_name: "prodkit.durable-work-item";
  readonly schema_version: "1.0.0";
  readonly job_id: string;
  readonly tenant_id: string;
  readonly queue: string;
  readonly kind: string;
  readonly idempotency_key: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly created_at: string;
  readonly available_at: string;
  readonly attempt: number;
  readonly max_attempts: number;
  readonly state: WorkState;
  readonly completed_at?: string | null;
  readonly last_error?: string | null;
}

export interface LeasedWorkItem {
  readonly schema_name: "prodkit.leased-work-item";
  readonly schema_version: "1.0.0";
  readonly item: DurableWorkItem;
  readonly lease: FencedLease;
}

export interface QueueSnapshot {
  readonly schema_name: "prodkit.queue-snapshot";
  readonly schema_version: "1.0.0";
  readonly queue: string;
  readonly tenant_id: string;
  readonly queued: number;
  readonly leased: number;
  readonly dead_letter: number;
  readonly succeeded: number;
  readonly captured_at: string;
}

export interface CapacityEnvelope {
  readonly schema_name: "prodkit.capacity-envelope";
  readonly schema_version: "1.0.0";
  readonly profile_id: string;
  readonly max_queue_depth: number;
  readonly max_in_flight: number;
  readonly max_per_tenant_in_flight: number;
  readonly lease_ttl_seconds: number;
  readonly shutdown_grace_seconds: number;
  readonly qualification_concurrency: number;
  readonly qualification_work_items: number;
  readonly qualification_soak_seconds: number;
}

export type ContentStorageMode = "none" | "hash_only" | "redacted" | "full";

export interface ArtifactRef {
  readonly tenant_id: string;
  readonly artifact_id: string;
  readonly media_type: string;
  readonly sha256: string;
  readonly size_bytes: number;
  readonly storage_mode: ContentStorageMode;
  readonly location?: string | null;
  readonly encrypted: boolean;
  readonly redacted: boolean;
  readonly redaction_version?: string | null;
  readonly retention_until?: string | null;
  readonly classification: string;
}

export type TenantAccessMode = "tenant" | "support";
export type TenantCapability =
  | "read"
  | "write"
  | "execute"
  | "approve"
  | "export"
  | "delete"
  | "legal_hold"
  | "configure";

export interface TenantAccessContext {
  readonly schema_name: "prodkit.tenant-access-context";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly actor: ActorRef;
  readonly mode: TenantAccessMode;
  readonly capabilities: readonly TenantCapability[];
  readonly elevation_id?: string | null;
  readonly reason?: string | null;
  readonly ticket_reference?: string | null;
  readonly issued_at: string;
  readonly expires_at?: string | null;
}

export interface SupportElevationGrant {
  readonly schema_name: "prodkit.support-elevation-grant";
  readonly schema_version: "1.0.0";
  readonly grant_id: string;
  readonly target_tenant_id: string;
  readonly operator: ActorRef;
  readonly issued_by: ActorRef;
  readonly capabilities: readonly TenantCapability[];
  readonly reason: string;
  readonly ticket_reference: string;
  readonly issued_at: string;
  readonly expires_at: string;
  readonly revoked_at?: string | null;
}

export interface TenantIsolationProfile {
  readonly schema_name: "prodkit.tenant-isolation-profile";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly policy_profile: string;
  readonly signing_profile: string;
  readonly retention_profile: string;
  readonly executor_profile: string;
  readonly storage_partition: string;
  readonly cache_namespace: string;
  readonly allow_support_access: boolean;
  readonly attributes: Readonly<Record<string, string>>;
}

export type TenantLifecycleStatus = "active" | "deletion_scheduled" | "deleted";

export interface TenantLifecycleRecord {
  readonly schema_name: "prodkit.tenant-lifecycle";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly status: TenantLifecycleStatus;
  readonly legal_hold: boolean;
  readonly deletion_not_before?: string | null;
  readonly updated_at: string;
  readonly updated_by: ActorRef;
  readonly elevation_id?: string | null;
}

export interface TenantExportManifest {
  readonly schema_name: "prodkit.tenant-export-manifest";
  readonly schema_version: "1.0.0";
  readonly export_id: string;
  readonly tenant_id: string;
  readonly created_at: string;
  readonly created_by: ActorRef;
  readonly elevation_id?: string | null;
  readonly record_counts: Readonly<Record<string, number>>;
  readonly content_digests: readonly string[];
  readonly legal_hold_preserved: boolean;
}

export type GovernanceRisk = "low" | "medium" | "high" | "critical";
export type GovernanceTargetType =
  | "retention_policy"
  | "trust_root_policy"
  | "legal_hold_release"
  | "tenant_configuration"
  | "compatibility_policy"
  | "deprecation_policy";
export type GovernanceChangeStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "applied"
  | "cancelled";
export type GovernanceApprovalDecision = "approve" | "reject";
export type RetentionDisposition = "retain" | "delete";
export type LegalHoldStatus = "active" | "released";
export type GovernanceAuditEventType =
  | "change_proposed"
  | "change_approved"
  | "change_rejected"
  | "change_applied"
  | "retention_evaluated"
  | "retention_deletion_intent_recorded"
  | "retention_deletion_cancelled"
  | "retention_deletion_executed"
  | "legal_hold_placed"
  | "legal_hold_released"
  | "trust_root_activated"
  | "evidence_export_created"
  | "evidence_import_verified"
  | "migration_recorded";

export interface RetentionRule {
  readonly resource_type: string;
  readonly retain_for_seconds?: number | null;
  readonly deletion_grace_seconds: number;
  readonly deletion_allowed: boolean;
}

export interface RetentionPolicy {
  readonly schema_name: "prodkit.retention-policy";
  readonly schema_version: "1.0.0";
  readonly policy_id: string;
  readonly tenant_id: string;
  readonly revision: number;
  readonly effective_at: string;
  readonly default_retain_for_seconds?: number | null;
  readonly rules: readonly RetentionRule[];
  readonly created_at: string;
  readonly created_by: ActorRef;
}

export interface RetentionCandidate {
  readonly schema_name: "prodkit.retention-candidate";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly resource_type: string;
  readonly resource_id: string;
  readonly created_at: string;
  readonly content_sha256?: string | null;
  readonly attributes: Readonly<Record<string, string>>;
}

export interface RetentionDecision {
  readonly schema_name: "prodkit.retention-decision";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly resource_type: string;
  readonly resource_id: string;
  readonly disposition: RetentionDisposition;
  readonly evaluated_at: string;
  readonly policy_id: string;
  readonly policy_revision: number;
  readonly delete_not_before?: string | null;
  readonly legal_hold_ids: readonly string[];
  readonly reason: string;
}

export interface RetentionExecutionRecord {
  readonly schema_name: "prodkit.retention-execution";
  readonly schema_version: "1.0.0";
  readonly execution_id: string;
  readonly tenant_id: string;
  readonly resource_type: string;
  readonly resource_id: string;
  readonly executed_at: string;
  readonly executed_by: ActorRef;
  readonly policy_id: string;
  readonly policy_revision: number;
  readonly content_sha256?: string | null;
  readonly deletion_reference: string;
}

export interface LegalHold {
  readonly schema_name: "prodkit.legal-hold";
  readonly schema_version: "1.0.0";
  readonly hold_id: string;
  readonly tenant_id: string;
  readonly status: LegalHoldStatus;
  readonly reason: string;
  readonly case_reference: string;
  readonly resource_types: readonly string[];
  readonly resource_ids: readonly string[];
  readonly placed_at: string;
  readonly placed_by: ActorRef;
  readonly released_at?: string | null;
  readonly released_by?: ActorRef | null;
  readonly release_change_request_id?: string | null;
}

export interface GovernanceChangeRequest {
  readonly schema_name: "prodkit.governance-change-request";
  readonly schema_version: "1.0.0";
  readonly request_id: string;
  readonly tenant_id: string;
  readonly target_type: GovernanceTargetType;
  readonly target_id: string;
  readonly proposed_digest: string;
  readonly expected_current_digest?: string | null;
  readonly risk: GovernanceRisk;
  readonly reason: string;
  readonly ticket_reference: string;
  readonly proposed_at: string;
  readonly proposed_by: ActorRef;
  readonly status: GovernanceChangeStatus;
  readonly approved_at?: string | null;
  readonly approved_by?: ActorRef | null;
  readonly applied_at?: string | null;
}

export interface GovernanceApproval {
  readonly schema_name: "prodkit.governance-approval";
  readonly schema_version: "1.0.0";
  readonly approval_id: string;
  readonly request_id: string;
  readonly tenant_id: string;
  readonly decision: GovernanceApprovalDecision;
  readonly actor: ActorRef;
  readonly occurred_at: string;
  readonly reason: string;
}

export type CheckpointSigningAlgorithm = "ed25519";

export interface TrustedSigningKey {
  readonly key_id: string;
  readonly algorithm: CheckpointSigningAlgorithm;
  readonly public_key_base64: string;
  readonly signer_id: string;
  readonly valid_from: string;
  readonly valid_until?: string | null;
  readonly revoked_at?: string | null;
}

export interface TrustRootPolicy {
  readonly schema_name: "prodkit.trust-root-policy";
  readonly schema_version: "1.0.0";
  readonly policy_id: string;
  readonly revision: string;
  readonly trusted_keys: readonly TrustedSigningKey[];
  readonly allowed_signers: readonly string[];
  readonly allow_historical_signatures_before_revocation: boolean;
}

export interface GovernedTrustRoot {
  readonly schema_name: "prodkit.governed-trust-root";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly revision: number;
  readonly policy: TrustRootPolicy;
  readonly policy_sha256: string;
  readonly activated_at: string;
  readonly retired_at?: string | null;
  readonly change_request_id: string;
}

export interface KeyRotationPlan {
  readonly schema_name: "prodkit.key-rotation-plan";
  readonly schema_version: "1.0.0";
  readonly rotation_id: string;
  readonly tenant_id: string;
  readonly from_revision: number;
  readonly to_revision: number;
  readonly activate_at: string;
  readonly overlap_until: string;
  readonly change_request_id: string;
  readonly emergency: boolean;
}

export interface TrustRootHistory {
  readonly schema_name: "prodkit.trust-root-history";
  readonly schema_version: "1.0.0";
  readonly tenant_id: string;
  readonly roots: readonly GovernedTrustRoot[];
}

export interface EvidenceTransferManifest {
  readonly schema_name: "prodkit.evidence-transfer-manifest";
  readonly schema_version: "1.0.0";
  readonly transfer_id: string;
  readonly tenant_id: string;
  readonly created_at: string;
  readonly created_by: ActorRef;
  readonly source_control_version: string;
  readonly source_schema_version: number;
  readonly archive_sha256: string;
  readonly bundle_manifest_sha256: string;
  readonly trust_root_revision?: number | null;
  readonly legal_hold_preserved: boolean;
}

export interface EvidenceImportReceipt {
  readonly schema_name: "prodkit.evidence-import-receipt";
  readonly schema_version: "1.0.0";
  readonly import_id: string;
  readonly transfer_id: string;
  readonly tenant_id: string;
  readonly imported_at: string;
  readonly imported_by: ActorRef;
  readonly source_control_version: string;
  readonly source_schema_version: number;
  readonly archive_sha256: string;
  readonly verification_id: string;
  readonly verification_sha256: string;
  readonly trust_anchor_sha256: string;
  readonly verified: true;
}

export interface EvidenceTransferVerification {
  readonly schema_name: "prodkit.evidence-transfer-verification";
  readonly schema_version: "1.0.0";
  readonly verification_id: string;
  readonly transfer_id: string;
  readonly tenant_id: string;
  readonly verified_at: string;
  readonly source_control_version: string;
  readonly source_schema_version: number;
  readonly package_sha256: string;
  readonly bundle_manifest_sha256: string;
  readonly trust_anchor_sha256: string;
  readonly verified_offline: true;
}

export interface MigrationPath {
  readonly from_schema_version: number;
  readonly to_schema_version: number;
  readonly minimum_control_version: string;
  readonly requires_backup: boolean;
  readonly reversible: boolean;
}

export interface DeprecationWindow {
  readonly surface: string;
  readonly deprecated_in_version: string;
  readonly removal_not_before_version: string;
  readonly announced_at: string;
  readonly replacement?: string | null;
}

export interface CompatibilityPolicy {
  readonly schema_name: "prodkit.compatibility-policy";
  readonly schema_version: "1.0.0";
  readonly current_schema_version: number;
  readonly minimum_supported_schema_version: number;
  readonly migration_paths: readonly MigrationPath[];
  readonly deprecations: readonly DeprecationWindow[];
}

export interface GovernanceAuditEvent {
  readonly schema_name: "prodkit.governance-audit-event";
  readonly schema_version: "1.0.0";
  readonly event_id: string;
  readonly tenant_id: string;
  readonly event_type: GovernanceAuditEventType;
  readonly actor: ActorRef;
  readonly occurred_at: string;
  readonly request_id?: string | null;
  readonly target_type?: GovernanceTargetType | null;
  readonly target_id?: string | null;
  readonly before_digest?: string | null;
  readonly after_digest?: string | null;
  readonly reason: string;
  readonly ticket_reference?: string | null;
  readonly attributes: Readonly<Record<string, string>>;
}



// v0.7.0 reliability and disaster-recovery contracts
export type RecoveryComponent =
  | "ledger"
  | "lineage"
  | "configuration"
  | "artifact_metadata"
  | "object_store"
  | "idempotency"
  | "execution_attempts"
  | "governance";
export type RecoveryIntegrityStatus = "verified" | "failed";
export type RestoreStatus = "verified" | "degraded" | "failed";
export type RecoveryFindingSeverity = "info" | "warning" | "error" | "critical";
export type UncertainRecoveryDisposition =
  | "reconcile_required"
  | "matched_success"
  | "matched_failure"
  | "not_observed"
  | "unverifiable";
export type BreakGlassCapability =
  | "restore"
  | "failover"
  | "integrity_scan"
  | "reconcile"
  | "configure_recovery";
export type RecoveryAuditEventType =
  | "profile_published"
  | "backup_recorded"
  | "restore_planned"
  | "break_glass_issued"
  | "break_glass_used"
  | "break_glass_revoked"
  | "integrity_scan_recorded"
  | "uncertain_attempt_reconciled"
  | "restore_completed"
  | "game_day_recorded";

export interface ReliabilityProfile {
  readonly schema_name: "prodkit.reliability-profile";
  readonly schema_version: "1.0.0";
  readonly profile_id: string;
  readonly tenant_id: string;
  readonly revision: number;
  readonly rpo_seconds: number;
  readonly rto_seconds: number;
  readonly backup_interval_seconds: number;
  readonly max_backup_age_seconds: number;
  readonly restore_exercise_interval_seconds: number;
  readonly required_components: readonly RecoveryComponent[];
  readonly max_break_glass_seconds: number;
  readonly effective_at: string;
  readonly created_at: string;
  readonly created_by: ActorRef;
}

export interface BackupComponentRecord {
  readonly component: RecoveryComponent;
  readonly reference: string;
  readonly sha256: string;
  readonly size_bytes: number;
  readonly captured_at: string;
  readonly source_site: string;
  readonly encrypted: boolean;
  readonly immutable: boolean;
}

export interface BackupManifest {
  readonly schema_name: "prodkit.backup-manifest";
  readonly schema_version: "1.0.0";
  readonly backup_id: string;
  readonly tenant_id: string;
  readonly profile_id: string;
  readonly profile_revision: number;
  readonly source_schema_version: number;
  readonly source_control_version: string;
  readonly source_site: string;
  readonly snapshot_set_id: string;
  readonly recovery_point_at: string;
  readonly created_at: string;
  readonly components: readonly BackupComponentRecord[];
  readonly ledger_chain_tip_sha256: string;
  readonly trusted_checkpoint_sha256: string;
  readonly trust_anchor_sha256: string;
}

export interface RestorePlan {
  readonly schema_name: "prodkit.restore-plan";
  readonly schema_version: "1.0.0";
  readonly restore_id: string;
  readonly tenant_id: string;
  readonly profile_id: string;
  readonly profile_revision: number;
  readonly backup_id: string;
  readonly target_site: string;
  readonly requested_at: string;
  readonly requested_by: ActorRef;
  readonly break_glass_grant_id: string;
  readonly reconcile_uncertain: true;
  readonly prohibit_blind_replay: true;
}

export interface RestoredComponentObservation {
  readonly component: RecoveryComponent;
  readonly reference: string;
  readonly sha256: string;
  readonly observed_at: string;
}

export interface RecoveryIntegrityFinding {
  readonly severity: RecoveryFindingSeverity;
  readonly component?: RecoveryComponent | null;
  readonly reference: string;
  readonly summary: string;
  readonly expected_sha256?: string | null;
  readonly observed_sha256?: string | null;
}

export interface IntegrityScanResult {
  readonly schema_name: "prodkit.recovery-integrity-scan";
  readonly schema_version: "1.0.0";
  readonly scan_id: string;
  readonly restore_id: string;
  readonly tenant_id: string;
  readonly completed_at: string;
  readonly status: RecoveryIntegrityStatus;
  readonly chain_verified: boolean;
  readonly trust_anchor_verified: boolean;
  readonly object_store_verified: boolean;
  readonly components_verified: readonly RecoveryComponent[];
  readonly findings: readonly RecoveryIntegrityFinding[];
}

export interface UncertainExecutionRecovery {
  readonly schema_name: "prodkit.uncertain-execution-recovery";
  readonly schema_version: "1.0.0";
  readonly recovery_id: string;
  readonly restore_id: string;
  readonly tenant_id: string;
  readonly attempt_id: string;
  readonly action_id: string;
  readonly run_id: string;
  readonly original_state: "uncertain";
  readonly provider_operation_id?: string | null;
  readonly disposition: UncertainRecoveryDisposition;
  readonly observed_at: string;
  readonly evidence_reference?: string | null;
  readonly replay_permitted: false;
}

export interface RestoreResult {
  readonly schema_name: "prodkit.restore-result";
  readonly schema_version: "1.0.0";
  readonly restore_id: string;
  readonly tenant_id: string;
  readonly backup_id: string;
  readonly started_at: string;
  readonly completed_at: string;
  readonly status: RestoreStatus;
  readonly actual_rpo_seconds: number;
  readonly actual_rto_seconds: number;
  readonly integrity_scan_id: string;
  readonly uncertain_recoveries: readonly UncertainExecutionRecovery[];
  readonly promoted: boolean;
  readonly completed_by: ActorRef;
}

export interface BreakGlassGrant {
  readonly schema_name: "prodkit.break-glass-grant";
  readonly schema_version: "1.0.0";
  readonly grant_id: string;
  readonly tenant_id: string;
  readonly operator: ActorRef;
  readonly approved_by: ActorRef;
  readonly capabilities: readonly BreakGlassCapability[];
  readonly reason: string;
  readonly ticket_reference: string;
  readonly issued_at: string;
  readonly expires_at: string;
}

export interface BreakGlassUse {
  readonly schema_name: "prodkit.break-glass-use";
  readonly schema_version: "1.0.0";
  readonly use_id: string;
  readonly grant_id: string;
  readonly tenant_id: string;
  readonly capability: BreakGlassCapability;
  readonly actor: ActorRef;
  readonly occurred_at: string;
  readonly purpose: string;
}

export interface GameDayExercise {
  readonly schema_name: "prodkit.dr-game-day";
  readonly schema_version: "1.0.0";
  readonly exercise_id: string;
  readonly tenant_id: string;
  readonly profile_id: string;
  readonly profile_revision: number;
  readonly backup_id: string;
  readonly restore_id: string;
  readonly started_at: string;
  readonly completed_at: string;
  readonly simulated_site_failure: boolean;
  readonly achieved_rpo_seconds: number;
  readonly achieved_rto_seconds: number;
  readonly chain_verified: boolean;
  readonly trust_anchor_verified: boolean;
  readonly uncertain_actions_reconciled: boolean;
  readonly blind_replay_count: number;
  readonly passed: boolean;
  readonly notes: readonly string[];
}

export interface RecoveryAuditEvent {
  readonly schema_name: "prodkit.recovery-audit-event";
  readonly schema_version: "1.0.0";
  readonly event_id: string;
  readonly tenant_id: string;
  readonly event_type: RecoveryAuditEventType;
  readonly actor: ActorRef;
  readonly occurred_at: string;
  readonly target_id: string;
  readonly reason: string;
  readonly ticket_reference?: string | null;
  readonly attributes: Readonly<Record<string, string>>;
}
