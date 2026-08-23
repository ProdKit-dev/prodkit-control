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
    ControlEvent,
    ExecutionResult,
    ExternalAuditEvent,
    ExternalStateObservation,
    InTotoStatementV1,
    LineageGraph,
    PolicyDecision,
    ProdKitEvidencePredicateV1,
    ProductionCompletenessAssessment,
    ProductionCompletenessProfile,
    ProductionLineageAssessment,
    ReconciliationBatch,
    ReconciliationCursor,
    ReconciliationFinding,
    ReconciliationRunResult,
    RetentionLockReceipt,
    RunRecord,
    SignedCheckpoint,
    SlsaProvenancePredicateV1,
    StateObservation,
    TrustRootPolicy,
    VerificationResult,
)

MODELS = {
    "action-spec.schema.json": ActionSpec,
    "approval-decision.schema.json": ApprovalDecision,
    "assurance-profile.schema.json": AssuranceProfile,
    "canonical-model-request.schema.json": CanonicalModelRequest,
    "canonical-model-response.schema.json": CanonicalModelResponse,
    "control-event.schema.json": ControlEvent,
    "execution-result.schema.json": ExecutionResult,
    "external-audit-event.schema.json": ExternalAuditEvent,
    "external-state-observation.schema.json": ExternalStateObservation,
    "in-toto-statement-v1.schema.json": InTotoStatementV1,
    "policy-decision.schema.json": PolicyDecision,
    "prodkit-evidence-predicate-v1.schema.json": ProdKitEvidencePredicateV1,
    "production-completeness-assessment.schema.json": ProductionCompletenessAssessment,
    "production-completeness-profile.schema.json": ProductionCompletenessProfile,
    "reconciliation-batch.schema.json": ReconciliationBatch,
    "reconciliation-cursor.schema.json": ReconciliationCursor,
    "reconciliation-finding.schema.json": ReconciliationFinding,
    "reconciliation-run-result.schema.json": ReconciliationRunResult,
    "retention-lock-receipt.schema.json": RetentionLockReceipt,
    "control-run.schema.json": RunRecord,
    "lineage-graph.schema.json": LineageGraph,
    "production-lineage-assessment.schema.json": ProductionLineageAssessment,
    "signed-checkpoint.schema.json": SignedCheckpoint,
    "slsa-provenance-predicate-v1.schema.json": SlsaProvenancePredicateV1,
    "state-observation.schema.json": StateObservation,
    "trust-root-policy.schema.json": TrustRootPolicy,
    "verification-result.schema.json": VerificationResult,
}


def render(model: type) -> str:
    return json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


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
