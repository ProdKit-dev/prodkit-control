# Roadmap

## 0.1 — Canonical foundation

- Canonical run, event, action, policy, approval, verification, reconciliation, and lineage contracts
- Typed specification-to-production lineage and fail-closed production completeness assessment
- Deterministic canonical JSON and hash-chained in-memory ledger
- Provider-neutral action broker and executor protocol
- Evidence bundle export and verification
- FastAPI and CLI surfaces
- PostgreSQL append-only adapter

## 0.2 — Hardened execution

- Durable idempotency and recovery after ambiguous executor failures
- Workload identity and short-lived credential leases
- Shell, filesystem, Git, GitHub, and HTTP executors
- OPA integration and digest-bound human approval service
- External artifact store encryption and retention policies

## 0.3 — Delivery-chain reconciliation

- GitHub and CI reconcilers
- Container registry, deployment, Kubernetes, and database reconcilers
- Unexpected-action detection
- Durable lineage persistence and organization-specific completeness profiles

## 0.4 — Attestations and interoperability

- in-toto Statement and SLSA-compatible provenance emission
- Sigstore signing and verification
- OpenTelemetry semantic projection
- MCP gateway and agent-framework adapters

## 1.0 — Production assurance profile

- Documented threat-model closure for the supported deployment profile
- Disaster recovery, key rotation, legal hold, and retention controls
- Multi-tenant isolation and authorization verification
- Independent security review
- Compatibility and migration policy
