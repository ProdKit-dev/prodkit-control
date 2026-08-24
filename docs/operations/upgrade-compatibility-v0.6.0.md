# v0.6.0 upgrade and compatibility policy

ProdKit Control v0.6.0 requires PostgreSQL schema **7** at runtime and fails closed when the schema is ahead or behind.

## Supported direct starts

- Schema 6 (v0.5) -> schema 7.
- Schema 5 (v0.4) -> schema 6 -> schema 7.

Both paths are exercised against PostgreSQL 18 in CI and must preserve pre-existing run ownership/state. Older schemas are outside the v0.6 direct-upgrade window and must first follow the earlier sequential upgrade path.

## Procedure

1. Stop or drain writers using the existing rolling-shutdown procedure.
2. Take a deployment-appropriate database backup and record its immutable reference.
3. Apply migrations sequentially; never skip a numbered migration.
4. Confirm `prodkit_schema_metadata.version = 7`.
5. Run application startup compatibility checks before admitting traffic.
6. Verify governance tables, existing tenant/run ownership, and append-only migration evidence.
7. Resume writers only after health/readiness and reconciliation checks pass.

## Rollback and deprecation

Schema downgrade is not supported. If migration cannot be forward-fixed, restore the pre-migration backup using the operator's database recovery procedure. Public surface deprecations must be represented by `DeprecationWindow` with an announced version, a removal-not-before version, and an optional replacement. A deprecated surface cannot be treated as removed before its declared window.

Disaster-recovery proof, scheduled restore exercises, and RPO/RTO guarantees remain v0.7 scope.
