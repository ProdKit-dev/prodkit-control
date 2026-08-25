# prodkit-control-postgres

`prodkit-control-postgres` is the durable PostgreSQL adapter set for ProdKit Control.

It persists append-only event and lineage evidence, execution-attempt and idempotency state, durable work and leases, reconciliation state, tenant controls, governance records, runs, and recovery state. The supported schema evolves through immutable migrations; previously published migrations must not be rewritten.

Repository qualification exercises the supported PostgreSQL 18 profile, including concurrency, tenant isolation, recovery, governance, and failover-sensitive invariants. Application semantics remain defined by the canonical contracts rather than by database-specific behavior.
