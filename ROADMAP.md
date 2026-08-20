# Roadmap

## 0.0.1 — Canonical foundation — 2026-08-20

- Canonical run, event, action, policy, approval, verification, reconciliation, and lineage contracts
- Typed specification-to-production lineage and fail-closed production completeness assessment
- Deterministic canonical JSON and hash-chained in-memory ledger
- Provider-neutral action broker and executor protocol
- Internally tamper-evident evidence bundles with optional external archive-digest trust anchors
- Fail-closed FastAPI authentication boundary with injectable principal resolution
- FastAPI and CLI surfaces
- PostgreSQL append-only adapter boundary
- Python and TypeScript package/contracts workspace normalized to `0.0.1`

## 0.1.0 — Hardened execution

- Durable idempotency recovery after ambiguous executor failures
- Workload identity and short-lived credential leases
- Production implementations of shell, filesystem, Git, GitHub, HTTP, and other controlled executors
- OPA integration and digest-bound human approval service
- External artifact-store encryption, retention, and legal-hold policies
- Durable authenticated HTTP deployment profile and transactional service wiring

## 0.2.0 — Delivery-chain reconciliation

- GitHub and CI reconcilers
- Container registry, deployment, Kubernetes, and database reconcilers
- Unexpected-action detection
- Durable lineage persistence and organization-specific completeness profiles

## 0.3.0 — Attestations and interoperability

- in-toto Statement and SLSA-compatible provenance emission
- Sigstore signing and verification with managed key/trust policy
- OpenTelemetry semantic projection
- MCP gateway and agent-framework adapters

## 1.0.0 — Production assurance profile

- Documented threat-model closure for the supported deployment profile
- Disaster recovery, key rotation, legal hold, and retention controls
- Multi-tenant isolation and authorization verification
- Independent security review
- Compatibility and migration policy
