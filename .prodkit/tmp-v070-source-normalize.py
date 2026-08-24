from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected source fragment missing from {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Runtime typing: break-glass operator is a canonical actor, not an opaque object.
recovery = Path(
    "packages/python/prodkit-control-runtime/src/prodkit_control_runtime/recovery.py"
)
text = recovery.read_text(encoding="utf-8")
text = text.replace(
    "    AuthorizationDeniedError,\n    BackupManifest,",
    "    ActorRef,\n    AuthorizationDeniedError,\n    BackupManifest,",
    1,
)
text = text.replace("        operator: object,", "        operator: ActorRef,", 1)
text = text.replace(
    "        from prodkit_control_core import ActorRef\n\n        self._require_tenant",
    "        self._require_tenant",
    1,
)
text = text.replace(
    "        if not isinstance(operator, ActorRef):\n            raise TypeError(\"operator must be ActorRef\")\n",
    "",
    1,
)
recovery.write_text(text, encoding="utf-8")

# Fresh PostgreSQL qualification must recognize schema 8.
replace_once(
    "scripts/ci_postgres.py",
    '            "0007_governance_lifecycle.sql",\n        ]:',
    '            "0007_governance_lifecycle.sql",\n            "0008_reliability_disaster_recovery.sql",\n        ]:',
)
replace_once(
    "scripts/ci_postgres.py",
    "        if version != 7:\n            raise AssertionError(f\"expected schema version 7, got {version!r}\")",
    "        if version != 8:\n            raise AssertionError(f\"expected schema version 8, got {version!r}\")",
)

# JSON Schema public surface.
replace_once(
    "scripts/export_schemas.py",
    "    ApprovalDecision,\n    AssuranceProfile,",
    "    ApprovalDecision,\n    AssuranceProfile,\n    BackupComponentRecord,\n    BackupManifest,\n    BreakGlassGrant,\n    BreakGlassUse,",
)
replace_once(
    "scripts/export_schemas.py",
    "    ProductionLineageAssessment,\n    QueueSnapshot,",
    "    ProductionLineageAssessment,\n    QueueSnapshot,\n    RecoveryAuditEvent,\n    RecoveryIntegrityFinding,\n    ReliabilityProfile,\n    RestoredComponentObservation,\n    RestorePlan,\n    RestoreResult,",
)
replace_once(
    "scripts/export_schemas.py",
    "    TenantLifecycleRecord,\n    TrustRootHistory,",
    "    TenantLifecycleRecord,\n    UncertainExecutionRecovery,\n    TrustRootHistory,",
)
replace_once(
    "scripts/export_schemas.py",
    '    "assurance-profile.schema.json": AssuranceProfile,\n',
    '    "assurance-profile.schema.json": AssuranceProfile,\n    "backup-component-record.schema.json": BackupComponentRecord,\n    "backup-manifest.schema.json": BackupManifest,\n    "break-glass-grant.schema.json": BreakGlassGrant,\n    "break-glass-use.schema.json": BreakGlassUse,\n',
)
replace_once(
    "scripts/export_schemas.py",
    '    "queue-snapshot.schema.json": QueueSnapshot,\n',
    '    "queue-snapshot.schema.json": QueueSnapshot,\n    "recovery-audit-event.schema.json": RecoveryAuditEvent,\n    "recovery-integrity-finding.schema.json": RecoveryIntegrityFinding,\n    "reliability-profile.schema.json": ReliabilityProfile,\n    "restored-component-observation.schema.json": RestoredComponentObservation,\n    "restore-plan.schema.json": RestorePlan,\n    "restore-result.schema.json": RestoreResult,\n',
)
replace_once(
    "scripts/export_schemas.py",
    '    "tenant-lifecycle-record.schema.json": TenantLifecycleRecord,\n',
    '    "tenant-lifecycle-record.schema.json": TenantLifecycleRecord,\n    "uncertain-execution-recovery.schema.json": UncertainExecutionRecovery,\n',
)
# IntegrityScanResult and GameDayExercise have useful standalone schemas too.
replace_once(
    "scripts/export_schemas.py",
    "    GovernedTrustRoot,\n    InTotoStatementV1,",
    "    GovernedTrustRoot,\n    GameDayExercise,\n    IntegrityScanResult,\n    InTotoStatementV1,",
)
replace_once(
    "scripts/export_schemas.py",
    '    "governed-trust-root.schema.json": GovernedTrustRoot,\n',
    '    "governed-trust-root.schema.json": GovernedTrustRoot,\n    "dr-game-day.schema.json": GameDayExercise,\n    "recovery-integrity-scan.schema.json": IntegrityScanResult,\n',
)

# TypeScript parity for every canonical v0.7 contract family.
ts = Path("packages/typescript/control/src/index.ts")
ts_text = ts.read_text(encoding="utf-8")
marker = "// v0.7.0 reliability and disaster-recovery contracts"
if marker not in ts_text:
    ts_text += r'''

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
'''
ts.write_text(ts_text, encoding="utf-8")
