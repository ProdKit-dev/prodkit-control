from __future__ import annotations

import argparse
import json
from pathlib import Path

from prodkit_control_core import (
    ActionSpec,
    ApprovalDecision,
    AssuranceProfile,
    CanonicalModelRequest,
    CanonicalModelResponse,
    CapacityEnvelope,
    CompatibilityPolicy,
    ControlEvent,
    DeprecationWindow,
    DurableWorkItem,
    EvidenceImportReceipt,
    EvidenceTransferManifest,
    EvidenceTransferVerification,
    ExecutionResult,
    ExternalAuditEvent,
    ExternalStateObservation,
    FencedLease,
    GovernanceApproval,
    GovernanceAuditEvent,
    GovernanceChangeRequest,
    GovernedTrustRoot,
    InTotoStatementV1,
    KeyRotationPlan,
    LeasedWorkItem,
    LegalHold,
    LineageGraph,
    MigrationPath,
    PolicyDecision,
    ProdKitEvidencePredicateV1,
    ProductionCompletenessAssessment,
    ProductionCompletenessProfile,
    ProductionLineageAssessment,
    QueueSnapshot,
    ReconciliationBatch,
    ReconciliationCursor,
    ReconciliationFinding,
    ReconciliationRunResult,
    RetentionCandidate,
    RetentionDecision,
    RetentionExecutionRecord,
    RetentionLockReceipt,
    RetentionPolicy,
    RetentionRule,
    RunRecord,
    SignedCheckpoint,
    SlsaProvenancePredicateV1,
    StateObservation,
    SupportElevationGrant,
    TenantAccessContext,
    TenantAuditEvent,
    TenantExportManifest,
    TenantIsolationProfile,
    TenantLifecycleRecord,
    TrustRootHistory,
    TrustRootPolicy,
    VerificationResult,
)

MODELS = {
    "action-spec.schema.json": ActionSpec,
    "approval-decision.schema.json": ApprovalDecision,
    "assurance-profile.schema.json": AssuranceProfile,
    "canonical-model-request.schema.json": CanonicalModelRequest,
    "canonical-model-response.schema.json": CanonicalModelResponse,
    "capacity-envelope.schema.json": CapacityEnvelope,
    "compatibility-policy.schema.json": CompatibilityPolicy,
    "control-event.schema.json": ControlEvent,
    "deprecation-window.schema.json": DeprecationWindow,
    "durable-work-item.schema.json": DurableWorkItem,
    "evidence-import-receipt.schema.json": EvidenceImportReceipt,
    "evidence-transfer-manifest.schema.json": EvidenceTransferManifest,
    "evidence-transfer-verification.schema.json": EvidenceTransferVerification,
    "execution-result.schema.json": ExecutionResult,
    "external-audit-event.schema.json": ExternalAuditEvent,
    "external-state-observation.schema.json": ExternalStateObservation,
    "fenced-lease.schema.json": FencedLease,
    "governance-approval.schema.json": GovernanceApproval,
    "governance-audit-event.schema.json": GovernanceAuditEvent,
    "governance-change-request.schema.json": GovernanceChangeRequest,
    "governed-trust-root.schema.json": GovernedTrustRoot,
    "in-toto-statement-v1.schema.json": InTotoStatementV1,
    "key-rotation-plan.schema.json": KeyRotationPlan,
    "leased-work-item.schema.json": LeasedWorkItem,
    "legal-hold.schema.json": LegalHold,
    "migration-path.schema.json": MigrationPath,
    "policy-decision.schema.json": PolicyDecision,
    "prodkit-evidence-predicate-v1.schema.json": ProdKitEvidencePredicateV1,
    "production-completeness-assessment.schema.json": ProductionCompletenessAssessment,
    "production-completeness-profile.schema.json": ProductionCompletenessProfile,
    "queue-snapshot.schema.json": QueueSnapshot,
    "reconciliation-batch.schema.json": ReconciliationBatch,
    "reconciliation-cursor.schema.json": ReconciliationCursor,
    "reconciliation-finding.schema.json": ReconciliationFinding,
    "reconciliation-run-result.schema.json": ReconciliationRunResult,
    "retention-candidate.schema.json": RetentionCandidate,
    "retention-decision.schema.json": RetentionDecision,
    "retention-execution-record.schema.json": RetentionExecutionRecord,
    "retention-lock-receipt.schema.json": RetentionLockReceipt,
    "retention-policy.schema.json": RetentionPolicy,
    "retention-rule.schema.json": RetentionRule,
    "control-run.schema.json": RunRecord,
    "lineage-graph.schema.json": LineageGraph,
    "production-lineage-assessment.schema.json": ProductionLineageAssessment,
    "signed-checkpoint.schema.json": SignedCheckpoint,
    "slsa-provenance-predicate-v1.schema.json": SlsaProvenancePredicateV1,
    "state-observation.schema.json": StateObservation,
    "support-elevation-grant.schema.json": SupportElevationGrant,
    "tenant-access-context.schema.json": TenantAccessContext,
    "tenant-audit-event.schema.json": TenantAuditEvent,
    "tenant-export-manifest.schema.json": TenantExportManifest,
    "tenant-isolation-profile.schema.json": TenantIsolationProfile,
    "tenant-lifecycle-record.schema.json": TenantLifecycleRecord,
    "trust-root-history.schema.json": TrustRootHistory,
    "trust-root-policy.schema.json": TrustRootPolicy,
    "verification-result.schema.json": VerificationResult,
}


def render(model: type) -> str:
    return (
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "schemas"
    root.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for filename, model in MODELS.items():
        expected = render(model)
        path = root / filename
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                drift.append(filename)
        else:
            path.write_text(expected, encoding="utf-8")
    if drift:
        print("Schema drift detected:")
        for filename in drift:
            print(f"- {filename}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
