# Threat model

## Assets

- production systems and credentials;
- canonical event history;
- approvals and policy bundles;
- prompts, tool arguments, outputs, and artifacts;
- signing keys and external audit anchors;
- tenant boundaries.

## Principal threats

1. An agent bypasses the action broker.
2. A model changes arguments after approval.
3. A compromised executor fabricates success.
4. A crash produces an ambiguous partial side effect.
5. An attacker reuses an idempotency key with different arguments.
6. A tenant reads or writes another tenant's run.
7. Secrets or personal data leak through logs or artifacts.
8. An administrator edits or replaces the event history.
9. Policy or approval services fail open.
10. External production activity has no corresponding ProdKit event.

## Required mitigations

- no direct production credentials for agents;
- short-lived workload identity and least privilege;
- digest-bound approvals and pre-state checks;
- durable idempotency and reconciliation after uncertainty;
- append-only database privileges and signed checkpoints;
- encryption, redaction, retention, and legal-hold policy;
- tenant authorization at every adapter boundary;
- external audit-log ingestion and unexpected-action alerts;
- independent security testing before production enablement.
