# Architecture overview

## Design objective

ProdKit records a provider-independent, content-addressed lineage from approved intent to observed
production state. It is not a wrapper around one model vendor, a replacement for delivery tools,
or a system that treats model-generated tool calls as trusted facts.

## Layers

1. **Intent:** immutable specification revisions, constraints, and decision sets.
2. **Generation:** generator identity, version, inputs, provider metadata, and exact source-tree digest.
3. **Verification and build:** durable requirements, results, builder identity, and artifact digest.
4. **Action control:** proposal, policy, exact approval, idempotency, and controlled execution.
5. **Deployment and observation:** deployment identity and independently observed production-state digest.
6. **Reconciliation:** comparison with external Git, CI, cloud, database, Kubernetes, and deployment evidence.
7. **Integrity:** typed lineage, append-only events, hash chaining, artifacts, signed checkpoints, and evidence bundles.
8. **Projection:** APIs, query models, OpenTelemetry, dashboards, alerts, and exports.

## Source of truth

The typed `LineageGraph`, append-only `ControlEvent` sequence, and content-addressed artifacts form
the canonical record. Provider logs, Git, traces, database projections, and external audit logs are
witnesses or projections. They cannot silently replace the canonical record.

## Failure model

The broker fails closed on missing policy decisions, invalid approvals, unregistered executors,
expired actions, tenant mismatches, idempotency conflicts, integrity failures, and incomplete
production lineage. Ambiguous executor outcomes must be reconciled rather than guessed.
