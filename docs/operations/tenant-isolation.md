# Tenant isolation operations

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
