# Architecture Decision Records

Architecture Decision Records (ADRs) capture decisions that materially affect ProdKit Control's trust model, canonical contracts, compatibility, deployment profiles, or expensive-to-reverse architecture.

## Accepted decisions

- [ADR 0001: Fenced recoverable work without expiring action idempotency](0001-fenced-recoverable-work.md)

## When an ADR is required

Create an ADR for changes to one or more of:

- canonical source-of-truth ownership;
- event, lineage, evidence, or action semantics;
- authorization, approval, or trust boundaries;
- idempotency, execution uncertainty, or recovery semantics;
- tenant isolation;
- credential ownership or privileged execution boundaries;
- evidence signing/trust anchoring;
- mandatory external dependencies;
- public adapter contracts with broad compatibility impact;
- supported production/enterprise deployment profile;
- migration, deprecation, or compatibility policy.

Routine implementation details that preserve an existing architectural contract do not need an ADR.

## Status lifecycle

Use one of:

- `proposed`
- `accepted`
- `rejected`
- `superseded`
- `deprecated`

An accepted ADR is immutable as historical decision evidence. If the decision changes, create a new ADR and mark the old one superseded rather than rewriting the original rationale.

## Naming

Use monotonically increasing four-digit numbers:

```text
0001-short-decision-title.md
0002-next-decision.md
```

Copy [0000-template.md](0000-template.md) when creating a new ADR.

## Required content

An ADR should explain:

- context/problem;
- decision drivers;
- chosen decision;
- alternatives considered;
- security/trust consequences;
- reliability/operations consequences;
- compatibility/migration consequences;
- evidence/tests needed to prove the decision;
- status and supersession links.

## Relationship to canonical documentation

ADRs explain **why** architecture changed. They do not replace the canonical architecture documents.

After an ADR is accepted, update the relevant architecture, security, operations, README, and roadmap documentation so the current architecture can be understood without reading every historical ADR.
