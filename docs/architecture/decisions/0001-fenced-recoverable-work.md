# ADR 0001: Fenced recoverable work without expiring action idempotency

- Status: Accepted
- Date: 2026-08-23
- Decision owners: ProdKit Control maintainers

## Context

ProdKit Control must survive replica, process, and host failure while preserving its strongest guarantee: control-plane failover must not cause an external action to be repeated merely because the original worker disappeared.

Two superficially similar ownership problems have different safety requirements:

1. scheduler ownership must expire so another healthy worker can continue recoverable work;
2. external-action idempotency must **not** expire merely because a worker disappears, because the external system may already have accepted the effect.

Treating both as one generic expiring lock creates a classic crash-after-effect duplication window.

## Decision

ProdKit separates the two mechanisms.

### Scheduler coordination

Recoverable scheduler work uses time-bounded `FencedLease` ownership. Every takeover increments a monotonically increasing fence token. Shared state transitions require the current lease identity/token and reject stale owners.

`DurableWorkQueue` stores bounded work durably, uses explicit retry budgets, and dead-letters exhausted work. PostgreSQL is the first durable implementation; the contracts remain provider-neutral and a standalone in-memory implementation is maintained.

### External effects

`IdempotencyStore` ownership remains durable and non-expiring. An `ActionBroker` attempt that may have produced an effect but lost its result remains owned and uncertain. It is reconciled before any decision to compensate or retry.

Automatic scheduler replay of externally effectful work is allowed only when the handler can prove replay safety through provider-enforced idempotency, a destination fencing/version precondition, or an equivalent operation-specific guarantee.

## Consequences

### Positive

- process failover can recover ordinary scheduler work;
- stale workers cannot acknowledge or mutate shared queue state;
- external-effect ambiguity does not become an automatic duplicate execution;
- PostgreSQL, another durable coordinator, and standalone memory implementations can share one contract;
- capacity/backpressure can be expressed independently of action authorization.

### Costs

- integrations that want automatic replay must explicitly support idempotency/fencing semantics;
- operators must monitor expired leases and dead-letter work;
- a durable scheduler is an additional production subsystem, even when implemented in the existing PostgreSQL database;
- exactly-once external effects still depend on the external system's contract and cannot be manufactured by a local lease.

## Alternatives considered

### Expire the existing action idempotency claim

Rejected. A worker can crash after an external provider commits but before the control plane records completion. Expiring the claim would allow a second worker to replay an effect whose outcome is unknown.

### Rely on a single leader process

Rejected as the primary architecture. A single leader reduces concurrency but creates unnecessary failover coupling and does not protect against stale execution after a partition. Leases/fences remain useful even when an orchestrator elects leaders.

### Require Redis, Temporal, Kubernetes Leases, or a hosted queue

Rejected as a core requirement. Those systems may implement the ports, but ProdKit remains standalone-capable and provider-neutral. PostgreSQL is a supported durable implementation because the control plane already uses it for canonical durable state.

### Claim generic exactly-once execution

Rejected. Exactly-once claims across arbitrary external systems are unsound without destination cooperation. ProdKit instead proves single-owner scheduler transitions, durable action idempotency, explicit uncertainty, and replay requirements.

## Compatibility and migration

The new contracts are additive. PostgreSQL migration `0005_high_availability.sql` adds scheduler tables without changing v0.3 event, action, idempotency, attempt, run, lineage, or reconciliation tables. Existing v0.3 processes can remain running while the additive migration is applied; new v0.4 processes require schema version 5 before becoming ready.

## Verification

The v0.4 release must retain tests that prove:

- one winner under concurrent lease acquisition;
- strictly increasing fencing tokens across takeover;
- stale-owner mutation rejection;
- bounded queue overload behavior;
- bounded retry/dead-letter behavior;
- failover after an acknowledged idempotent external effect does not duplicate the effect identity;
- production PostgreSQL behavior matches the standalone contract;
- graceful drain rejects new work while allowing admitted work to finish.
