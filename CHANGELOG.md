# Changelog

All notable changes will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning after 1.0.

## [Unreleased]

## [0.3.0] - 2026-08-23

### Added

- in-toto Statement v1 and SLSA provenance-v1 interoperability contracts with a ProdKit evidence predicate and forward-compatible external-standard parsing.
- Standalone Ed25519 signed checkpoints, trust-root/key validity and revocation policy, assurance profiles, and independent offline verification.
- Portable evidence packages containing the evidence bundle, attestation, checkpoint, trust-root metadata, and retention-lock receipt with per-member SHA-256 verification and independent trust anchoring.
- Hardened Cosign/Sigstore blob signing and verification integration with key/keyless identity constraints, custom trust roots, offline verification, timeouts, and fail-closed process handling.
- MCP and framework-neutral agent adapters that convert tool/function calls into deterministic `ActionSpec` proposals without bypassing policy, approval, credential, execution, observation, or evidence boundaries.
- Conjunctive multi-policy composition for OPA, Permit/AuthZen-style, and custom policy engines.
- Bounded OpenTelemetry semantic projection with predictable error status/type handling and no arbitrary event-payload projection.

### Changed

- External interoperability models tolerate unknown compatible fields while authoritative ProdKit trust, checkpoint, retention, and assurance contracts remain strict.
- v0.3 targets the stable `https://in-toto.io/Statement/v1` and `https://slsa.dev/provenance/v1` wire identifiers instead of binding ProdKit architecture to transient standard minor versions.
- Policy composition is fail closed: `DENY` dominates `REQUIRE_APPROVAL`, which dominates `ALLOW`; approval roles are unioned and conflicting constraints deny.

### Security

- Portable packages do not trust embedded signing keys by themselves; verification requires an independently supplied trust-root policy or trust-root digest.
- Key validity, signer identity, revocation time, checkpoint signatures, evidence/attestation digests, retention mode/duration, archive membership, and size limits are verified before portable evidence is accepted.
- MCP/agent effect class, risk class, executor, operation, and target scope are administrator-owned bindings rather than model-supplied annotations.
- Required signing or retention controls fail closed; local storage is not mislabeled as compliance-grade WORM retention.

### Release scope

The unreleased v0.3.0 milestone is portable attestations and interoperability. The release remains incomplete until its exact-head CI/Security/CodeQL gates and the roadmap's offline verification, key-rotation/revocation, cross-version, and interoperability fixture gates are all evidenced.

## [0.2.0] - 2026-08-22

### Added

- Delivery-chain reconciliation across Git, GitHub, CI/build, registries, deployments, Kubernetes, and database/control-plane evidence.
- Canonical external state/audit contracts, deterministic findings, durable cursors, audit-event deduplication, and organization/tenant production-completeness profiles.
- PostgreSQL schema version 4 with PostgreSQL 18 durability coverage for reconciliation state.
- Configurable polling, freshness, capped exponential backoff, provider-shaped fixtures, and documented reconciliation SLO/escalation policy.

### Changed

- Reconciliation is fail-closed for stale, unavailable, conflicting, missing, and unexpected external evidence.
- Production completeness can require fresh healthy matched evidence from an explicit organization/tenant source set.

### Security

- Delivery-chain activity without a controlled action produces a high-severity `unexpected_external_action` finding.
- Conflicting evidence is explicit `conflicting_evidence`; one observation is never silently selected.

### Release scope

`v0.2.0` is the delivery-chain reconciliation milestone. Signed/interoperable provenance and key management remain `v0.3.0`.


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
