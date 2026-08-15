# Guarantees and non-guarantees

## Designed guarantees

With a durable ledger, controlled credentials, hardened executors, policy, approval, signed
checkpoints, and external reconciliation, ProdKit is designed to provide:

- ordered reconstruction of routed actions;
- exact policy and approval provenance;
- deterministic action and artifact fingerprints;
- typed linkage from specification revisions through observed production state;
- fail-closed assessment of required production-lineage stages;
- duplicate-side-effect protection through idempotency;
- before/after evidence and effect verification;
- mismatch detection against independent audit systems;
- provider-independent evidence export.

## Non-guarantees

ProdKit cannot guarantee completeness when:

- agents or humans possess credentials that bypass controlled executors;
- audit sources are disabled or mutable;
- content is retained only as hashes and the original content is lost;
- the host, database administrator, signing keys, and external audit anchors are all compromised;
- executor implementations misreport effects and no independent reconciliation exists;
- sampled telemetry is mistaken for the canonical ledger.
- lineage nodes are asserted without independent evidence or bypassed delivery activity is hidden.

## Claim language

Deployments should say **"traceable for changes and actions routed through ProdKit Control"**, not
"complete for every change in the organization," unless bypass prevention, durable external
anchors, and independent detection have been established.
