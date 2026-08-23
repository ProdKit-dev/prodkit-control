from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "0.5.0"
NEW = "0.6.0"
DATE = "2026-08-24"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path.relative_to(ROOT)}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Keep released migrations immutable. Repair schema-specific trigger discovery only in new schema 7.
migration = (
    ROOT
    / "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/migrations/0007_governance_lifecycle.sql"
)
text = migration.read_text(encoding="utf-8")
old_trigger_check = "SELECT 1 FROM pg_trigger WHERE tgname = trigger_name AND NOT tgisinternal"
new_trigger_check = (
    "SELECT 1 FROM pg_trigger "
    "WHERE tgname = trigger_name "
    "AND tgrelid = to_regclass(table_name) "
    "AND NOT tgisinternal"
)
count = text.count(old_trigger_check)
if count != 2:
    raise SystemExit(f"expected two schema-7 trigger checks, found {count}")
migration.write_text(text.replace(old_trigger_check, new_trigger_check), encoding="utf-8")

# Normalize every first-party Python package and workspace version.
python_projects = [
    ROOT / "pyproject.toml",
    *sorted((ROOT / "packages/python").glob("**/pyproject.toml")),
]
changed_projects = 0
for path in python_projects:
    text = path.read_text(encoding="utf-8")
    marker = f'version = "{OLD}"'
    if marker in text:
        path.write_text(text.replace(marker, f'version = "{NEW}"', 1), encoding="utf-8")
        changed_projects += 1
if changed_projects < 30:
    raise SystemExit(f"unexpected Python version surface: changed only {changed_projects} projects")

# Public Python module versions.
changed_modules = 0
for path in sorted((ROOT / "packages/python").glob("**/__init__.py")):
    text = path.read_text(encoding="utf-8")
    marker = f'__version__ = "{OLD}"'
    if marker in text:
        path.write_text(text.replace(marker, f'__version__ = "{NEW}"', 1), encoding="utf-8")
        changed_modules += 1
if changed_modules < 15:
    raise SystemExit(
        f"unexpected Python __version__ surface: changed only {changed_modules} modules"
    )

# FastAPI metadata is part of the release contract.
app = ROOT / "packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py"
replace_once(app, f'version="{OLD}"', f'version="{NEW}"')

# TypeScript package versions.
ts_packages = sorted((ROOT / "packages/typescript").glob("**/package.json"))
if len(ts_packages) != 4:
    raise SystemExit(f"expected four TypeScript packages, found {len(ts_packages)}")
for path in ts_packages:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != OLD:
        raise SystemExit(
            f"unexpected version in {path.relative_to(ROOT)}: {payload.get('version')!r}"
        )
    payload["version"] = NEW
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Keep the TypeScript canonical contract package at parity with the Python governance surface.
ts_index = ROOT / "packages/typescript/control/src/index.ts"
ts_text = ts_index.read_text(encoding="utf-8")
if "export type GovernanceRisk =" in ts_text:
    raise SystemExit("TypeScript governance contracts already exist unexpectedly")
ts_governance = r"""

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
  readonly verified_offline: boolean;
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
"""
ts_index.write_text(ts_text.rstrip() + ts_governance + "\n", encoding="utf-8")

# Changelog release entry.
changelog = ROOT / "CHANGELOG.md"
changelog_text = changelog.read_text(encoding="utf-8")
if f"## [{NEW}]" in changelog_text:
    raise SystemExit("v0.6.0 changelog section already exists unexpectedly")
entry = f"""## [{NEW}] - {DATE}

### Added

- Canonical governance, retention, legal-hold, trust-root lifecycle, evidence-transfer, compatibility, migration, and deprecation contracts with Python, TypeScript, and JSON Schema surfaces.
- Standalone and PostgreSQL governance stores with digest-bound change requests, append-only approval/audit evidence, independent approval for high/critical changes, and tenant-scoped policy history.
- Versioned retention policies with deterministic retain/delete decisions, scoped legal holds, bounded deletion adapters, and durable retention-execution receipts.
- Governed trust-root history and key-rotation plans that preserve historical checkpoint verification across explicit activation/retirement windows.
- Independently anchored evidence transfer verification plus durable import/export receipts.
- PostgreSQL schema 7 and qualification for supported schema 5 -> 7 and schema 6 -> 7 upgrades with durable-row preservation.

### Changed

- Governance mutations are ordinary-tenant authority only; support elevation cannot propose, approve, apply, release, delete, or rotate governance state.
- Retention deletion and legal-hold/policy mutation serialize on the same tenant governance lock, eliminating hold-vs-delete check/use races in the supported store profiles.
- Active scoped governance legal holds also block tenant lifecycle deletion at the database boundary.
- The supported direct database upgrade window for v0.6.0 is schema 5 or schema 6 to schema 7; older schemas must first follow the previously supported sequential upgrade path.

### Security

- High/critical configuration changes require a distinct approver identity and are bound to the exact proposed digest and optional expected-current digest.
- Governance approvals, policy revisions, evidence transfers/imports, retention executions, audit events, and migration evidence are append-only in PostgreSQL.
- Legal-hold release and trust-root retirement are constrained state transitions; raw database updates cannot silently rewrite immutable proposal, hold-scope, or trust-policy documents.
- Portable evidence import requires independent trust anchoring and produces verification evidence tied to exact package, manifest, tenant, schema, and trust-anchor digests.

### Release scope

`v0.6.0` implements the governance, retention, and lifecycle engineering milestone. Disaster recovery, RPO/RTO validation, restore exercises, and regional recovery remain v0.7.0 scope. The unrecorded independent v0.5 tenant-isolation review remains a separate claim-language gate.
"""
changelog.write_text(
    changelog_text.replace("## [Unreleased]\n", "## [Unreleased]\n\n" + entry + "\n", 1),
    encoding="utf-8",
)

# Roadmap status records implementation without bypassing release qualification.
roadmap = ROOT / "ROADMAP.md"
roadmap_text = roadmap.read_text(encoding="utf-8")
needle = "## v0.6.0 — Governance, retention, and lifecycle\n\n### Goal"
replacement = (
    "## v0.6.0 — Governance, retention, and lifecycle\n\n"
    "**Status:** Implemented in v0.6.0; release remains subject to the exact-candidate gates below.\n\n"
    "### Goal"
)
if needle not in roadmap_text:
    raise SystemExit("v0.6.0 roadmap section was not in the expected state")
roadmap.write_text(roadmap_text.replace(needle, replacement, 1), encoding="utf-8")

release_notes = ROOT / "docs/releases/v0.6.0.md"
release_notes.parent.mkdir(parents=True, exist_ok=True)
release_notes.write_text(
    """# ProdKit Control v0.6.0\n\n## Milestone\n\n`v0.6.0` is the governance, retention, and lifecycle milestone. It turns long-lived evidence and high-risk administrative changes into typed, versioned, auditable control-plane state rather than operator convention.\n\n## Production boundary\n\n- High/critical governance changes require independent approval bound to the proposed digest.\n- Retention evaluation is tenant scoped and legal hold takes precedence over deletion.\n- Retention deletion is serialized with legal-hold/policy mutation in the supported standalone and PostgreSQL profiles.\n- Governance audit, approval, policy revision, transfer/import, retention-execution, and migration evidence is append-only in PostgreSQL.\n- Trust-root revisions retain historical verification windows across rotation.\n- Evidence import is accepted only after independently anchored portable-package verification.\n- Schema 7 is the v0.6 runtime schema. Directly qualified upgrade starts are schema 5 and schema 6.\n\n## Release qualification\n\nThe exact release candidate must pass Python 3.12/3.13/3.14, Node 22/24, PostgreSQL 18, schema drift, strict typing, unit/integration tests, migration-path qualification, Security, CodeQL, trusted release proof, publication, and independent release verification.\n\n## Compatibility\n\nA v0.6 runtime fails closed against a database schema other than schema 7. Upgrade is sequential and additive. The release qualifies 5 -> 6 -> 7 and 6 -> 7. Deployments older than schema 5 must first use the earlier supported upgrade sequence. Downgrade is not claimed as a supported data migration path; forward-fix or pre-migration restore is the operator recovery model.\n\n## Claim boundaries\n\nThis release does not claim v0.7 disaster-recovery/RPO/RTO assurance. It also does not convert the still-unrecorded independent v0.5 tenant-isolation review into a completed review.\n""",
    encoding="utf-8",
)

upgrade_doc = ROOT / "docs/operations/upgrade-compatibility-v0.6.0.md"
upgrade_doc.parent.mkdir(parents=True, exist_ok=True)
upgrade_doc.write_text(
    """# v0.6.0 upgrade and compatibility policy\n\nProdKit Control v0.6.0 requires PostgreSQL schema **7** at runtime and fails closed when the schema is ahead or behind.\n\n## Supported direct starts\n\n- Schema 6 (v0.5) -> schema 7.\n- Schema 5 (v0.4) -> schema 6 -> schema 7.\n\nBoth paths are exercised against PostgreSQL 18 in CI and must preserve pre-existing run ownership/state. Older schemas are outside the v0.6 direct-upgrade window and must first follow the earlier sequential upgrade path.\n\n## Procedure\n\n1. Stop or drain writers using the existing rolling-shutdown procedure.\n2. Take a deployment-appropriate database backup and record its immutable reference.\n3. Apply migrations sequentially; never skip a numbered migration.\n4. Confirm `prodkit_schema_metadata.version = 7`.\n5. Run application startup compatibility checks before admitting traffic.\n6. Verify governance tables, existing tenant/run ownership, and append-only migration evidence.\n7. Resume writers only after health/readiness and reconciliation checks pass.\n\n## Rollback and deprecation\n\nSchema downgrade is not supported. If migration cannot be forward-fixed, restore the pre-migration backup using the operator's database recovery procedure. Public surface deprecations must be represented by `DeprecationWindow` with an announced version, a removal-not-before version, and an optional replacement. A deprecated surface cannot be treated as removed before its declared window.\n\nDisaster-recovery proof, scheduled restore exercises, and RPO/RTO guarantees remain v0.7 scope.\n""",
    encoding="utf-8",
)
