# Changelog

All notable changes will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning after 1.0.

## [Unreleased]

## [0.1.0] - 2026-08-21

### Added

- Durable PostgreSQL run state, tenant-scoped idempotency ownership, execution-attempt journaling, and schema compatibility metadata through schema version 3.
- Broker-owned execution-attempt identifiers and explicit `claimed`, `started`, `succeeded`, `failed`, and `uncertain` lifecycle evidence.
- OIDC/JWT principal resolution with issuer, audience, signature, expiry, tenant, actor-kind, and role validation.
- HTTPS approval and credential-lease providers with digest/policy/tenant bindings and short-lived workload credential references.
- AES-256-GCM encrypted filesystem artifact storage with authenticated ciphertext, tenant partitioning, redaction envelopes, atomic persistence, and retention metadata.
- Hardened filesystem, Git, GitHub, and HTTP executor implementations for the supported v0.1 production allowlist.
- Real PostgreSQL 18 durability CI and exact-source Trusted Release Proof coverage.
- Centralized lifecycle callers for runner policy, CI, Security, CodeQL, Trusted Release Proof, Release, and Release Metadata through `prodkit-workflows`.

### Changed

- Action execution now refuses automatic retries after ambiguous external execution and preserves the idempotency claim for operator reconciliation.
- Durable profiles require attempt-aware executors; credential-enabled profiles additionally require credential-lease-aware executors.
- Run coordination can use the durable PostgreSQL run store and append-only event ledger instead of process-local state.
- Release publication now requires successful CI, Security, and CodeQL on the exact current `main` SHA plus an exact-source Trusted Release Proof.
- Runner routing is delegated to the centralized organization workflow policy with trusted self-hosted fallback.

### Security

- Production API identity is fail-closed unless a principal resolver is configured; insecure header identity remains an explicit development-only opt-in.
- Approval decisions are checked against action/target digests, policy decision and revision, tenant, environment, role, and expiry.
- Privileged credentials remain behind the workload credential boundary; the control plane carries only non-secret lease references.
- Credential revocation failure after execution is treated as uncertain execution rather than successful completion.

### Release scope

`v0.1.0` is the hardened-execution milestone. Placeholder database, Kubernetes, and deployment executor packages are not part of the supported production executor allowlist for this release. Delivery-chain reconciliation remains `v0.2.0`, and signed/interoperable provenance remains `v0.3.0`.

## [0.0.0] - 2026-08-20

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
  to the first repository release version `0.0.0`, including `uv.lock` and exported schemas.
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

`0.0.0` is the canonical control-plane foundation. It does not claim that placeholder provider, executor,
reconciler, signing, or external identity adapters are complete production integrations. Durable recovery,
credential leasing, external reconciliation, signing/key management, and production deployment profiles are
tracked as later milestones.
