from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "0.6.1"
NEW = "0.7.0"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected {label} in {path.relative_to(ROOT)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all_if_present(path: Path, old: str, new: str) -> int:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count:
        path.write_text(text.replace(old, new), encoding="utf-8")
    return count


# Normalize every first-party Python project version.
pyprojects = [ROOT / "pyproject.toml", *sorted((ROOT / "packages/python").glob("**/pyproject.toml"))]
for path in pyprojects:
    replace_once(path, f'version = "{OLD}"', f'version = "{NEW}"', "Python project version")

# Normalize every published TypeScript package version without rewriting package.json formatting.
for path in sorted((ROOT / "packages/typescript").glob("**/package.json")):
    replace_once(path, f'"version": "{OLD}"', f'"version": "{NEW}"', "TypeScript package version")

# Runtime-visible first-party __version__ surfaces must agree with package metadata.
version_modules = 0
for path in sorted((ROOT / "packages/python").glob("**/__init__.py")):
    version_modules += replace_all_if_present(
        path,
        f'__version__ = "{OLD}"',
        f'__version__ = "{NEW}"',
    )
if version_modules == 0:
    raise SystemExit("no runtime __version__ surfaces were normalized")

replace_once(
    ROOT / "packages/python/prodkit-control-fastapi/src/prodkit_control_fastapi/app.py",
    f'version="{OLD}"',
    f'version="{NEW}"',
    "FastAPI version",
)

# PostgreSQL recovery qualification must create the tenant-owned run required by the
# existing tenant-isolation FK before journaling an execution attempt.
ci = ROOT / "scripts/ci_recovery_postgres.py"
text = ci.read_text(encoding="utf-8")
text = text.replace(
    "    RestoreStatus,\n    TenantAccessContext,",
    "    RestoreStatus,\n    RunRecord,\n    RunStatus,\n    TenantAccessContext,",
    1,
)
text = text.replace(
    "    PostgresExecutionAttemptStore,\n    PostgresRecoveryStore,",
    "    PostgresExecutionAttemptStore,\n    PostgresRecoveryStore,\n    PostgresRunStore,",
    1,
)
old_attempt = '''    attempts = PostgresExecutionAttemptStore(sessions)\n    attempt_id, action_id, run_id = uuid4(), uuid4(), uuid4()\n    claimed_at = datetime.now(UTC)\n    claimed = _attempt(\n'''
new_attempt = '''    attempts = PostgresExecutionAttemptStore(sessions)\n    runs = PostgresRunStore(sessions)\n    attempt_id, action_id, run_id = uuid4(), uuid4(), uuid4()\n    claimed_at = datetime.now(UTC)\n    await runs.create(\n        RunRecord(\n            run_id=run_id,\n            tenant_id=tenant_id,\n            status=RunStatus.RUNNING,\n            initiated_by=admin.actor,\n            environment="recovery-ci",\n            purpose="qualify durable uncertain execution recovery",\n            trace_id=f"recovery-ci-{run_id.hex}",\n            started_at=claimed_at,\n        )\n    )\n    claimed = _attempt(\n'''
if old_attempt not in text:
    raise SystemExit("missing recovery execution-attempt qualification setup")
text = text.replace(old_attempt, new_attempt, 1)
ci.write_text(text, encoding="utf-8")

# Changelog: release scope, assurance changes, and bounded claim language.
changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
if "## [0.7.0] - " not in text:
    marker = "## [Unreleased]\n\n"
    if marker not in text:
        raise SystemExit("CHANGELOG Unreleased marker missing")
    section = '''## [0.7.0] - 2026-08-24\n\n### Added\n\n- Canonical reliability, backup/restore, integrity, recovery-gap, break-glass, uncertain-execution and DR game-day contracts with Python, TypeScript and JSON Schema parity.\n- PostgreSQL schema 8 durable recovery catalog for profiles, backup manifests, break-glass evidence, restore plans/results, integrity scans, uncertain-attempt recovery, RPO-gap reconciliation, game-day exercises and audit evidence.\n- Provider-neutral PostgreSQL 18 disaster-recovery game-day qualification restoring isolated component bytes and proving signed-checkpoint, trust-anchor and object-store integrity.\n- Architecture and operator runbooks for the supported enterprise warm-standby recovery profile.\n\n### Changed\n\n- Verified restore now requires component/ledger integrity, cryptographic verification of the exact backed-up signed checkpoint, an independently supplied trust-root policy, object-store verification, reconciliation of every durable `UNCERTAIN` attempt, and explicit recovery-point-gap reconciliation.\n- Final promotion consumes a separately revalidated `FAILOVER` break-glass capability; earlier restore/reconciliation authority cannot silently become production failover authority.\n- PostgreSQL recovery timing and break-glass validity use database time, while authoritative uncertain-attempt candidates come from durable tenant-scoped execution state rather than caller selection.\n- Runtime schema compatibility advances from schema 7 to schema 8; the supported v0.7 milestone upgrade is schema 7 -> 8.\n\n### Security / reliability\n\n- Recovery never authorizes blind replay of ambiguous external effects. The RPO-gap barrier covers effects that may have occurred after the recovered snapshot but before failure detection.\n- Break-glass authority is tenant-bound, short-lived, four-eyes, capability-scoped, revocable, revalidated on every privileged stage, and unavailable through support elevation.\n- Recovery evidence is append-only and contradictory per-restore uncertain-attempt resolutions are rejected by durable uniqueness constraints.\n\n### Release scope\n\nThe qualified reference warm-standby profile targets RPO <= 300 seconds and RTO <= 3600 seconds under the repository's deterministic PostgreSQL 18 game-day conditions. These values are not a universal deployment SLA. v0.8 security/operational hardening and the separate independent v0.5 tenant-isolation review remain outside this release claim.\n\n'''
    text = text.replace(marker, marker + section, 1)
    changelog.write_text(text, encoding="utf-8")

# Roadmap milestone status.
roadmap = ROOT / "ROADMAP.md"
text = roadmap.read_text(encoding="utf-8")
heading = "## v0.7.0 — Reliability and disaster recovery\n\n"
status = "**Status:** Implemented in v0.7.0; release remains subject to exact-candidate proof, immutable publication, and release-verification gates.\n\n"
if status not in text:
    if heading not in text:
        raise SystemExit("v0.7 roadmap heading missing")
    text = text.replace(heading, heading + status, 1)
    roadmap.write_text(text, encoding="utf-8")

# README maturity text had drifted at v0.4. Replace the complete status section so public
# release-facing documentation matches the current engineering boundary.
readme = ROOT / "README.md"
text = readme.read_text(encoding="utf-8")
start = text.find("## Project status and maturity\n")
end = text.find("## Why this repository exists\n")
if start < 0 or end < 0 or end <= start:
    raise SystemExit("README maturity section boundaries missing")
status_section = '''## Project status and maturity\n\n`v0.7.0` is the **reliability and disaster-recovery milestone**. The repository includes the canonical foundation, hardened execution, delivery-chain reconciliation, portable assurance, HA/scale, multi-tenant isolation engineering controls, governance/lifecycle, and a qualified provider-neutral recovery control plane. The supported enterprise assurance claim remains maturity-gated; v0.8-v1.0 cover further security/operational hardening, production-candidate proof, and independent assurance.\n\n| Capability | v0.7.0 status | Next target |\n| --- | --- | --- |\n| Canonical contracts, typed lineage, hash-chained evidence | Implemented | Compatibility hardening |\n| Durable action execution and uncertainty handling | Implemented | Broader executor qualification |\n| Delivery-chain reconciliation | Implemented | Additional provider coverage |\n| Portable attestations and offline verification | Implemented | Higher-assurance trust profiles |\n| HA fencing, durable bounded work, backpressure, graceful drain | Implemented and qualified | Operational hardening |\n| Multi-tenant isolation engineering profile | Implemented; independent-review claim still gated | Independent review |\n| Governance, retention, legal hold, key/trust-root lifecycle | Implemented | Compliance/hardening expansion |\n| Reliability, backup/restore, uncertain-effect recovery, DR game day | Implemented and qualified reference profile | Production deployment exercises |\n| Python/TypeScript canonical surfaces | Implemented | Compatibility policy expansion |\n| Security/operational hardening and independent assurance | Roadmap | v0.8-v1.0 |\n\nBefore enabling production actions, read [Guarantees and non-guarantees](docs/architecture/guarantees.md), [Reliability and disaster recovery](docs/architecture/reliability-disaster-recovery.md), [the DR runbook](docs/operations/disaster-recovery.md), [Secure deployment](docs/security/secure-deployment.md), and [the roadmap](ROADMAP.md).\n\n'''
text = text[:start] + status_section + text[end:]
text = text.replace(
    "The diagram is the control-plane architecture through `v0.4.0`: durable execution, reconciliation, portable assurance, and HA scheduling are implemented behind provider-neutral ports. Later roadmap gates focus on DR, stronger enterprise isolation, governance/compliance packs, and independent assurance.",
    "The diagram is the provider-neutral control-plane foundation. Through `v0.7.0`, durable execution, reconciliation, portable assurance, HA scheduling, tenant-isolation engineering controls, governance/lifecycle, and reliability/disaster-recovery controls are implemented behind explicit contracts and adapters. Later roadmap gates focus on security/operational hardening and independent production assurance.",
)
readme.write_text(text, encoding="utf-8")

print(f"normalized {len(pyprojects)} Python projects, {version_modules} runtime version surfaces, and v0.7 release metadata")
