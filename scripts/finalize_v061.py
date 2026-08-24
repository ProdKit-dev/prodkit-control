from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "0.6.0"
NEW = "0.6.1"
DATE = "2026-08-24"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path.relative_to(ROOT)}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# First-party Python package versions.
for path in [ROOT / "pyproject.toml", *sorted((ROOT / "packages/python").glob("**/pyproject.toml"))]:
    text = path.read_text(encoding="utf-8")
    marker = f'version = "{OLD}"'
    if marker not in text:
        raise SystemExit(f"expected {OLD} project version in {path.relative_to(ROOT)}")
    path.write_text(text.replace(marker, f'version = "{NEW}"', 1), encoding="utf-8")

# Runtime/package version exports.
for path in sorted((ROOT / "packages/python").glob("**/__init__.py")):
    text = path.read_text(encoding="utf-8")
    updated = text.replace(f'__version__ = "{OLD}"', f'__version__ = "{NEW}"')
    if updated != text:
        path.write_text(updated, encoding="utf-8")

app = ROOT / "packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py"
replace_once(app, f'version="{OLD}"', f'version="{NEW}"')

# First-party TypeScript package versions.
for path in sorted((ROOT / "packages/typescript").glob("**/package.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != OLD:
        raise SystemExit(f"expected {OLD} package version in {path.relative_to(ROOT)}")
    payload["version"] = NEW
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

# Changelog: patch release supersedes, but does not rewrite, immutable v0.6.0.
changelog = ROOT / "CHANGELOG.md"
insert = f'''## [{NEW}] - {DATE}

### Security

- Evidence import now requires consumed `EvidenceTransferVerification` evidence and binds the import receipt to the exact verification ID, canonical verification digest, and independent trust-anchor digest.
- Destructive retention execution rejects caller-supplied evaluation timestamps and uses authoritative current time, preventing future-time acceleration of retention eligibility.
- Retention batches reject duplicate resource identities so destructive decisions cannot alias across distinct candidate objects.
- PostgreSQL destructive retention commits append-only deletion intent before the provider effect, then reacquires the tenant governance lock and revalidates current policy and legal holds immediately before deletion.
- If governed state changes before execution, deletion is cancelled with durable evidence; if provider outcome or final persistence is ambiguous, the pre-committed intent remains available for reconciliation.

### Changed

- `EvidenceImportReceipt` carries verification provenance (`verification_id`, `verification_sha256`, and `trust_anchor_sha256`).
- `EvidenceTransferVerification.verified_offline` is a fail-closed invariant rather than caller-controlled claim state.
- Governance operations documentation now treats deletion intent as a durable reconciliation boundary and requires explicit verification evidence for imports.

### Release scope

`v0.6.1` supersedes `v0.6.0` for governance retention deletion and evidence-import deployments. PostgreSQL schema 7 and the v0.6 compatibility window are unchanged. The immutable `v0.6.0` tag is not moved or rewritten.

'''
text = changelog.read_text(encoding="utf-8")
needle = "## [Unreleased]\n\n"
if f"## [{NEW}] - " not in text:
    if needle not in text:
        raise SystemExit("CHANGELOG Unreleased heading not found")
    changelog.write_text(text.replace(needle, needle + insert, 1), encoding="utf-8")

roadmap = ROOT / "ROADMAP.md"
replace_once(
    roadmap,
    "**Status:** Implemented in v0.6.0; release remains subject to the exact-candidate gates below.",
    "**Status:** Implemented in v0.6.0; v0.6.1 closes the post-release governance safety review findings while preserving the same schema-7 milestone boundary.",
)

architecture = ROOT / "docs/architecture/governance-lifecycle.md"
replace_once(
    architecture,
    "ProdKit Control v0.6.0 adds a provider-neutral governance plane",
    "ProdKit Control v0.6.1 maintains the v0.6 provider-neutral governance plane",
)
replace_once(
    architecture,
    "A retention decision is not itself a delete. `execute_retention` re-evaluates under the governance lock, records the decision, invokes the adapter only for a `delete` disposition, then appends a `RetentionExecutionRecord` and governance audit event. A hold committed before deletion obtains the same lock first and therefore forces `retain`.",
    "A retention decision is not itself a delete. Destructive `execute_retention` rejects caller-supplied evaluation time and uses authoritative current time. PostgreSQL first evaluates under the tenant governance lock and commits append-only deletion intent. It then reacquires the same lock, re-reads current policy and legal holds, and invokes the adapter only if the exact governed eligibility still holds. A hold or policy change committed first therefore cancels deletion. If the provider effect or final persistence is ambiguous, the pre-committed intent remains durable reconciliation evidence rather than disappearing with a rolled-back execution transaction.",
)
replace_once(
    architecture,
    "An import must also satisfy the v0.6 compatibility window. Verification does not automatically make imported evidence authoritative for a production decision; consumers must preserve source identity, trust anchors, and any applicable legal hold/retention metadata.",
    "An import must also satisfy the v0.6 compatibility window. The import API consumes the exact `EvidenceTransferVerification`; the resulting receipt binds its verification ID, canonical verification digest, and trust-anchor digest. A caller cannot create authoritative import evidence merely by supplying matching archive metadata. Verification still does not automatically make imported evidence authoritative for a production decision; consumers must preserve source identity, trust anchors, and any applicable legal hold/retention metadata.",
)

operations = ROOT / "docs/operations/governance-lifecycle.md"
replace_once(
    operations,
    "This runbook covers the v0.6.0 governance profile.",
    "This runbook covers the v0.6.1 governance profile.",
)
replace_once(
    operations,
    "The execution path re-evaluates under the tenant governance lock. A legal hold committed first prevents deletion. The adapter must use an idempotency mechanism or provider-native conditional delete where available; retrying an ambiguous destructive effect without reconciliation is not allowed.",
    "Destructive execution uses authoritative current time; callers cannot advance eligibility with a supplied evaluation timestamp. PostgreSQL evaluates under the tenant governance lock and commits append-only deletion intent before any provider effect. It then reacquires the lock and revalidates current policy and legal holds immediately before invoking the adapter. A hold or policy change committed first cancels deletion. The adapter must use an idempotency mechanism or provider-native conditional delete where available. If provider outcome or final receipt persistence is ambiguous, reconcile the durable intent before any retry.",
)
replace_once(
    operations,
    "Embedded trust metadata alone is not a trust anchor.",
    "The import call must consume the exact offline `EvidenceTransferVerification`; the durable import receipt records its verification ID, canonical digest, and independent trust-anchor digest. Embedded trust metadata alone is not a trust anchor.",
)

print("v0.6.1 release metadata staged")
