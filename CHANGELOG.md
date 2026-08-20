# Changelog

All notable changes will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning after 1.0.

## [Unreleased]

## [0.0.1] - 2026-08-20

### Added

- Canonical run, event, action, policy, approval, verification, reconciliation, and lineage contracts.
- Typed, content-addressed specification-to-production lineage with fail-closed completeness assessment.
- Hash-chained event ledger, provider-neutral action broker, controlled executor protocol, FastAPI surface,
  CLI, PostgreSQL adapter boundary, JSON Schemas, and TypeScript contracts.
- External SHA-256 trust-anchor verification for evidence-bundle archives.
- Injectable authenticated-principal resolution for the HTTP API with tenant, actor, and approval-role binding.
- Explicit `execution.uncertain` evidence for ambiguous executor failures while retaining idempotency claims.
- Regression coverage for idempotent evidence reuse, run transition integrity, API fail-closed behavior,
  and evidence trust anchors.

### Changed

- Normalized every first-party Python and TypeScript package from the initialization version `0.1.0`
  to the first repository release version `0.0.1`, including `uv.lock` and exported schemas.
- Run creation/completion now validates actor tenancy before state mutation; completion accepts terminal
  states only and appends the audit event before committing in-memory state.
- Execution results, state observations, and verification results are validated as bound to the requested action.
- In-progress idempotency conflicts use the dedicated duplicate-action error rather than approval semantics.
- Header-based API identity is disabled by default and is available only through an explicit development opt-in.
- CI and CodeQL use the repository's supported self-hosted runner profile and current action generations.

### Security

- Evidence bundles are described as internally tamper-evident, not self-authenticating or cryptographically
  signed; callers can pin verification to a trusted external archive digest.
- Approval roles are derived from authenticated principal claims and intersected with policy-required roles,
  instead of being accepted from request bodies.

### Release scope

`0.0.1` is the canonical control-plane foundation. It does not claim that placeholder provider, executor,
reconciler, signing, or external identity adapters are complete production integrations. Durable recovery,
credential leasing, external reconciliation, signing/key management, and production deployment profiles are
tracked as later milestones.
