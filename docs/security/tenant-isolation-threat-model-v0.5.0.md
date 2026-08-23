# v0.5.0 tenant isolation threat model

## Protected assets

Tenant-owned runs, events, lineage, execution attempts, reconciliation state, artifacts, durable jobs, cache entries, isolation configuration, support grants, lifecycle state, export manifests, and administrative audit evidence are protected from unauthorized cross-tenant disclosure or mutation.

## Trust boundaries

Production tenant identity originates at authenticated ingress and remains explicit through service, repository, adapter, persistence, task, cache, artifact, and evidence boundaries. Globally unique identifiers do not grant authority. Platform support identity is distinct from tenant identity and requires explicit, tenant-scoped elevation.

## Primary threats and controls

- **Foreign identifier disclosure:** every supported tenant-data lookup carries a tenant predicate and known foreign identifiers return tenant-local not-found/empty results.
- **Tenant reassignment:** PostgreSQL composite ownership constraints and tenant-immutability triggers prevent changing a mutable row's `tenant_id`.
- **Artifact reference substitution:** tenant identity is part of the artifact reference, path partition, and AES-GCM authenticated data.
- **Cache/task confusion:** cache namespaces and durable queue acquisition include mandatory tenant scope.
- **Support privilege escalation:** support access requires tenant opt-in, trusted issuer/operator attributes, short TTL, exact capabilities, reason/ticket binding, live grant revalidation, and auditable elevation identity. Support cannot enable its own access by modifying the isolation profile.
- **Stale support authorization:** revocation, expiry, operator mismatch, or tenant opt-out invalidates subsequent use immediately.
- **Lifecycle bypass:** legal hold precedes deletion scheduling; deletion is future-time gated and recorded as a separate lifecycle transition.
- **Audit destruction:** tenant administration audit events and export manifests are append-only in the durable schema.
- **Cross-tenant operational wildcard:** normal queue/snapshot APIs require a concrete tenant; no implicit all-tenant behavior is exposed by omitting a predicate.

## Residual boundaries

ProdKit Control cannot make an external provider tenant-isolated if that provider adapter uses shared credentials or unscoped provider resources incorrectly. Production deployments must configure tenant-appropriate policy, signing, retention, executor, storage, and provider scopes. End-to-end deletion also depends on downstream providers implementing the selected deletion/retention semantics.

Independent external review is not implied by this threat model; see `tenant-isolation-review-v0.5.0.md`.
