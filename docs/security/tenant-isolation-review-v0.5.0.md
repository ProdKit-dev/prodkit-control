# v0.5.0 tenant-isolation security review packet

**Review status:** external independent review not yet recorded.

This document defines the review target; it is not itself an independent security review and must not be cited as one.

## Review target

Review the exact v0.5.0 candidate for authenticated tenant derivation, mandatory repository predicates, PostgreSQL composite ownership constraints, tenant immutability, event/lineage/task/cache/artifact isolation, support-elevation authorization and revocation, tenant-specific configuration, export/deletion/legal-hold semantics, and audit evidence integrity.

## Adversarial cases

Attempt access with known valid foreign run, event, lineage, action, execution-attempt, job, support-grant, export, and audit identifiers; cross-tenant artifact-reference substitution; namespace confusion; forged/stale support contexts; use after revocation, expiry, or tenant opt-out; support self-enablement; legal-hold bypass; SQL tenant reassignment; and mutation/deletion of append-only audit/export evidence.

## Evidence to inspect

Inspect exact-candidate CI, Security, CodeQL, PostgreSQL 18 tenant qualification, Trusted Release Proof, migration 0006, tenant-control stores, canonical schemas, release assets, and release verification output. Findings should record severity, affected boundary, reproducibility, and required release-claim changes.

## Claim gate

Until an external reviewer and review artifact are recorded, v0.5.0 may be described as implementing and automatically qualifying its multi-tenant isolation profile, but not as independently reviewed, certified, audited, or externally validated.
