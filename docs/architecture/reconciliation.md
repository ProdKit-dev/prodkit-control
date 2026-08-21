# Delivery-chain reconciliation

ProdKit Control reconciliation compares the controlled intent-to-production record with independent
evidence from the delivery chain. It is deliberately fail-closed: missing, stale, unavailable, or
conflicting evidence is never translated into a successful production assessment.

## Evidence plane

Provider adapters normalize external records into two canonical evidence types:

- **state observations** describe the externally observed state of a controlled action;
- **audit events** describe external mutations or provider activity, including activity that cannot be
  associated with a ProdKit action.

The v0.2.0 adapters cover Git, GitHub, CI/build systems, package/container registries, deployment
systems, Kubernetes, and database/control-plane sources. Fetching and provider authentication remain
host-integration responsibilities; adapters normalize already-authorized provider responses so the
reconciliation engine stays provider-neutral.

## Outcomes

The engine emits exactly explicit outcomes:

- `matched` — fresh healthy evidence agrees with the controlled expectation;
- `missing_external_evidence` — a controlled action has no corresponding provider evidence;
- `unexpected_external_action` — provider state/audit activity has no controlled action;
- `state_mismatch` — provider state exists but its digest differs;
- `conflicting_evidence` — independent observations disagree;
- `unverifiable` — the source is stale, unavailable, or otherwise cannot support a conclusion.

Unknown and unavailable states are therefore non-green by construction.

## Incremental operation

Each tenant/source pair owns a durable cursor containing its provider cursor, high-water mark, source
health, failure count, and next-attempt time. Successful polls advance the cursor and return to the
configured polling interval. Collection failures use capped exponential backoff. Provider audit-event
identity is `(tenant_id, source_system, event_id)`, making ingestion idempotent.

Reconciliation findings have deterministic IDs derived from run, source, outcome, controlled action,
external reference, and evidence discriminator. Reprocessing the same evidence therefore does not
create duplicate findings.

## Production completeness

A tenant-scoped production-completeness profile declares the required evidence sources and maximum
acceptable source age. Production is complete only when every required source is fresh and healthy,
there are no blocking reconciliation findings, and—when enabled by the profile—each source has a
matched reconciliation finding.

Completeness profiles do not weaken lineage requirements. They are an external-evidence requirement
layer on top of the existing specification → verification → build → authorization → action →
deployment → observation lineage.

## SLOs and escalation

Defaults are intentionally conservative and configurable per source:

| Control | Default |
| --- | --- |
| Poll interval | 5 minutes |
| Source stale threshold | 15 minutes |
| First failure retry | 30 seconds |
| Maximum backoff | 30 minutes |

Operational policy:

- `unexpected_external_action`, `state_mismatch`, and `conflicting_evidence` are **high severity** and
  should page/escalate for production-scoped tenants on the next completed reconciliation cycle.
- `unverifiable` from an unavailable/conflicting source is **high severity**; a merely stale source is
  at least **medium severity**.
- a source crossing its stale threshold immediately stops satisfying production completeness;
- repeated collection failures never downgrade severity or preserve a previous green result;
- operators should investigate high/critical findings before approving further production mutation.

A five-minute polling interval is a detection target, not a statement that every upstream provider
delivers evidence within five minutes. Host applications should tighten source-specific schedules for
higher-risk environments.
