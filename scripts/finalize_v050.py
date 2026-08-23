from __future__ import annotations

import json
import re
from pathlib import Path

VERSION = "0.5.0"


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


# Synchronize first-party Python package versions.
for path in [Path("pyproject.toml"), *sorted(Path("packages/python").glob("**/pyproject.toml"))]:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^version = "0\.4\.0"$', f'version = "{VERSION}"', text)
    path.write_text(text, encoding="utf-8")

# Synchronize first-party TypeScript package versions.
for path in sorted(Path("packages/typescript").glob("**/package.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") == "0.4.0":
        data["version"] = VERSION
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Synchronize runtime version exports.
for path in sorted(Path("packages/python").glob("**/__init__.py")):
    text = path.read_text(encoding="utf-8")
    if '__version__ = "0.4.0"' in text:
        path.write_text(
            text.replace('__version__ = "0.4.0"', f'__version__ = "{VERSION}"'),
            encoding="utf-8",
        )

app = Path("packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py")
app.write_text(
    app.read_text(encoding="utf-8").replace('version="0.4.0"', f'version="{VERSION}"'),
    encoding="utf-8",
)

# Export every canonical v0.5 tenant contract as JSON Schema.
export = Path("scripts/export_schemas.py")
text = export.read_text(encoding="utf-8")
text = text.replace(
    "    StateObservation,\n    TrustRootPolicy,",
    "    StateObservation,\n    SupportElevationGrant,\n    TenantAccessContext,\n"
    "    TenantAuditEvent,\n    TenantExportManifest,\n    TenantIsolationProfile,\n"
    "    TenantLifecycleRecord,\n    TrustRootPolicy,",
)
text = text.replace(
    '    "state-observation.schema.json": StateObservation,\n',
    '    "state-observation.schema.json": StateObservation,\n'
    '    "support-elevation-grant.schema.json": SupportElevationGrant,\n'
    '    "tenant-access-context.schema.json": TenantAccessContext,\n'
    '    "tenant-audit-event.schema.json": TenantAuditEvent,\n'
    '    "tenant-export-manifest.schema.json": TenantExportManifest,\n'
    '    "tenant-isolation-profile.schema.json": TenantIsolationProfile,\n'
    '    "tenant-lifecycle-record.schema.json": TenantLifecycleRecord,\n',
)
export.write_text(text, encoding="utf-8")

# Release changelog and roadmap status.
changelog = Path("CHANGELOG.md")
text = changelog.read_text(encoding="utf-8")
if "## [0.5.0] - 2026-08-23" not in text:
    section = """## [0.5.0] - 2026-08-23

### Added

- Canonical tenant access, isolation profile, support-elevation, lifecycle, export, and audit contracts with Python, TypeScript, and JSON Schema surfaces.
- Durable PostgreSQL tenant-control state for isolation profiles, support grants, legal hold/deletion lifecycle, export manifests, and tenant administration audit evidence.
- Tenant-bound evidence bundles, artifact references and encryption authentication context, cache namespaces, execution attempts, lineage, events, runs, and durable work acquisition.
- Known-foreign-ID negative/property qualification plus PostgreSQL 18 tests for tenant partitions, live grant revocation, lifecycle precedence, append-only audit/export evidence, and immutable tenant ownership.

### Changed

- Repository, service, storage, queue, event, lineage, attempt, artifact, and reconciliation APIs require explicit tenant scope instead of relying on globally unique identifiers.
- Durable queues and snapshots require a concrete tenant; cross-tenant operational aggregation is reserved for an explicitly privileged administrative surface.
- PostgreSQL schema version 6 adds tenant-first indexes, composite ownership constraints, immutable tenant ownership, and durable tenant-governance tables.
- Support elevation is opt-in, time-bounded, exact-capability scoped, reason/ticket bound, revalidated on every use, and cannot modify the isolation profile that authorizes support access.

### Security

- Known valid foreign identifiers resolve as tenant-local not-found or empty results rather than leaking another tenant's resource existence.
- AES-GCM artifact authentication binds tenant identity, preventing a valid encrypted artifact from being replayed under another tenant reference.
- Grant revocation, expiry, operator identity, tenant opt-in, reason/ticket binding, and exact capability are checked at privileged-use time.
- Tenant audit events and export manifests are append-only in PostgreSQL; mutable tenant-owned rows cannot be reassigned to another tenant.

### Release scope

`v0.5.0` implements the multi-tenant enterprise-isolation engineering milestone. It does not claim that an independent tenant-isolation security review has been completed. Wording such as “independently reviewed enterprise isolation” remains blocked until an external review is completed and recorded.

"""
    changelog.write_text(
        text.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n" + section),
        encoding="utf-8",
    )

roadmap = Path("ROADMAP.md")
text = roadmap.read_text(encoding="utf-8")
marker = "## v0.5.0 — Multi-tenant enterprise isolation\n\n"
status = (
    "**Status:** Implemented in v0.5.0. Independent-review claim language remains gated "
    "on a recorded external tenant-isolation review.\n\n"
)
if marker in text and status not in text:
    roadmap.write_text(text.replace(marker, marker + status), encoding="utf-8")

write(
    "docs/releases/v0.5.0.md",
    """# ProdKit Control v0.5.0

ProdKit Control v0.5.0 makes tenant isolation an enforced control-plane property across authenticated identity, repositories, persistence, jobs, events, caches, artifacts, support access, configuration, and tenant lifecycle state.

## Isolation boundary

Tenant identity is derived from authenticated principal context at ingress and remains mandatory at repository and durable-store boundaries. Knowing a valid foreign run, action, attempt, lineage, job, artifact, reconciliation, export, or audit identifier does not grant visibility or mutation authority.

The standalone profile preserves the same tenant-scoped contracts for embedded use, development, and deterministic qualification. The supported durable multi-tenant profile uses PostgreSQL schema version 6 and `PostgresTenantControlStore`, adding database-enforced ownership beneath service predicates.

## Durable tenant control

Schema version 6 persists tenant-local isolation profiles, support elevation grants, lifecycle state, append-only administrative audit evidence, and append-only export manifests. Isolation profiles select policy, signing, retention, executor, storage partition, and cache namespace behavior without coupling ProdKit Control to one provider.

Support elevation is tenant opt-in, short-lived, exact-capability scoped, ticket/reason bound, and revalidated against current durable state on every use. Revocation, expiry, tenant opt-out, operator mismatch, or capability mismatch fails closed. Support elevation cannot change the tenant isolation profile that controls support opt-in.

## Lifecycle semantics

Tenant export creates a durable manifest and audit record. Legal hold takes precedence over deletion scheduling. Deletion is explicit and time-gated. Tenant audit and export evidence cannot be updated or deleted in the PostgreSQL profile, and mutable tenant-owned rows cannot be reassigned to another tenant.

## Qualification

The v0.5 qualification suite covers known foreign identifiers, tenant-scoped event and lineage reads, tenant-partitioned durable work, artifact tenant binding, cache namespace separation, support opt-in/revocation/capabilities, legal-hold precedence, export/audit isolation, PostgreSQL ownership constraints, append-only evidence, Python 3.12/3.13/3.14, Node.js 22/24, PostgreSQL 18, Security, and CodeQL.

## Claim boundary

This release implements and automatically qualifies the v0.5 multi-tenant isolation engineering profile. It does **not** claim that the profile has been independently security reviewed. The roadmap's independent-review gate must be satisfied before wording such as “independently reviewed enterprise isolation” is used.

## Release gates

Publication requires the exact candidate to pass CI, Security, CodeQL, Trusted Release Proof, immutable release publication, and independent published-asset verification.
""",
)

write(
    "docs/operations/tenant-isolation.md",
    """# Tenant isolation operations

## Supported profiles

The standalone profile is suitable for development, embedded deployments, and deterministic qualification. Horizontally scaled production deployments must use PostgreSQL schema version 6 and the durable tenant-control store; process-local tenant-governance state is not a production substitute.

## Identity and data access

Production ingress must derive tenant identity from an authenticated principal resolver. Tenant IDs supplied only in untrusted request bodies or headers are not authoritative. Keep tenant predicates at every repository, event, lineage, attempt, artifact, task, reconciliation, and governance boundary even when identifiers are globally unique.

## Storage, artifacts, and caches

Artifact paths are tenant partitioned and tenant identity is included in AES-GCM authenticated data. Cache keys must use `TenantCacheNamespace`. Database composite ownership constraints and immutability triggers provide defense in depth beneath application predicates.

## Support elevation

A tenant must opt in through its isolation profile. A trusted support authority may issue a short-lived exact-capability grant to a trusted support operator for one tenant, with a reason and ticket reference. Every privileged use revalidates the live durable grant and current tenant opt-in. Revocation or tenant opt-out is immediately effective. Support elevation cannot modify the isolation profile that enables support access.

## Export, legal hold, and deletion

Export creates a tenant-bound manifest and audit evidence. Legal hold blocks deletion scheduling and takes precedence over existing schedules. Deletion requires an explicit future not-before time and a separate completion transition. Downstream providers must implement equivalent tenant-local retention/deletion behavior before end-to-end deletion is claimed.

## Migration

Migration 0006 is additive for v0.5. Apply it before starting v0.5 replicas. v0.5 requires schema version 6. After v0.5 replicas are ready, drain older replicas. Do not move rows between tenants by rewriting `tenant_id`; database triggers reject ownership reassignment.
""",
)

write(
    "docs/security/tenant-isolation-review-v0.5.0.md",
    """# v0.5.0 tenant-isolation security review packet

**Review status:** external independent review not yet recorded.

This document defines the review target; it is not itself an independent security review and must not be cited as one.

## Review target

Review the exact v0.5.0 candidate for authenticated tenant derivation, mandatory repository predicates, PostgreSQL composite ownership constraints, tenant immutability, event/lineage/task/cache/artifact isolation, support-elevation authorization and revocation, tenant-specific configuration, export/deletion/legal-hold semantics, and audit evidence integrity.

## Adversarial cases

Attempt access with known valid foreign run, event, lineage, action, execution-attempt, job, support-grant, export, and audit identifiers; cross-tenant artifact-reference substitution; namespace confusion; forged/stale support contexts; use after revocation, expiry, or tenant opt-out; support self-enablement; legal-hold bypass; SQL tenant reassignment; and mutation/deletion of append-only audit/export evidence.

## Evidence to inspect

Inspect exact-candidate CI, Security, CodeQL, PostgreSQL 18 tenant qualification, Trusted Release Proof, migration 0006, tenant-control stores, canonical schemas, release assets, and release verification output. Findings should record severity, affected boundary, reproducibility, and required release-claim changes.

## Claim gate

Until an external reviewer and review artifact are recorded, v0.5.0 may be described as implementing and automatically qualifying its multi-tenant isolation profile, but not as independently reviewed, certified, audited, or externally validated.
""",
)
