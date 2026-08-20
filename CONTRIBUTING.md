# Contributing

Thank you for contributing to ProdKit Control.

## Development process

1. Open an issue for significant behavior, contract, trust-boundary, or deployment-profile changes.
2. Add or update an Architecture Decision Record (ADR) when the change meets the criteria in [`docs/architecture/decisions/README.md`](docs/architecture/decisions/README.md).
3. Keep provider- and vendor-specific behavior behind explicit adapter boundaries; do not move vendor authority into the canonical core.
4. Update the canonical architecture/security/operations documentation when implementation changes an invariant, guarantee, failure mode, or supported profile.
5. Add deterministic tests for lifecycle transitions and failure modes introduced or changed by the contribution.
6. Add integration/adversarial/migration tests when the change affects external effects, durable recovery, tenancy, credentials, signing, reconciliation, or compatibility.
7. Run `make check` before submitting a pull request.

## Architecture rules

Contributions must preserve the current canonical invariants unless an accepted ADR explicitly changes them. In particular:

- models/agents are untrusted proposers, not implicit authorities;
- approval binds to exact action/context;
- required control/evidence dependencies fail closed;
- uncertain external effects are not treated as safe-to-retry failures;
- canonical events are append-only;
- lineage relations remain typed and endpoint-constrained;
- production tenant identity comes from authenticated context;
- telemetry does not replace canonical evidence;
- vendor adapters cannot weaken core authorization/integrity semantics;
- package existence does not imply production maturity.

Start with [`docs/architecture/README.md`](docs/architecture/README.md) and [`docs/architecture/overview.md`](docs/architecture/overview.md).

## ADR process

Use [`docs/architecture/decisions/0000-template.md`](docs/architecture/decisions/0000-template.md) for changes that materially affect:

- canonical source-of-truth ownership;
- event/lineage/action/evidence semantics;
- authorization/trust/credential boundaries;
- idempotency or uncertain-execution recovery;
- multi-tenant isolation;
- signing/trust anchors;
- mandatory dependencies;
- public adapter compatibility;
- supported production/enterprise profile;
- migration/deprecation/compatibility policy.

Accepted ADRs are historical decision records. Supersede them with a new ADR rather than rewriting accepted rationale.

## Compatibility rules

- Canonical event and lineage schemas use explicit versions.
- Existing fields are not repurposed to mean something materially different.
- Breaking contract changes require an appropriate new schema/API major version.
- Generated JSON Schemas must be committed and drift-checked in CI.
- Events are immutable after append; corrections are represented by new events.
- Lineage identities and relations are immutable historical assertions; corrections add superseding assertions/events rather than rewriting history.
- Security-critical readers should reject unsupported semantics rather than silently guess.
- Pre-1.0 compatibility may evolve, but breaking changes still require explicit release notes and migration impact.
- The 1.0 production profile must publish a supported compatibility, migration, and deprecation policy.

## Testing expectations

### Core/contract changes

Add deterministic unit/property tests for validation, canonicalization, hashing, state transitions, and negative cases.

### External-effect changes

Add tests for:

- idempotency key/digest mismatch;
- timeout/crash/uncertain outcomes;
- target precondition changes;
- retry safety;
- external operation identity/evidence;
- reconciliation when applicable.

### Security/tenancy changes

Add negative tests proving unauthorized/cross-tenant access and capability escalation fail closed.

### Storage/migration changes

Add migration/upgrade tests and recovery behavior for in-flight/uncertain actions where relevant.

## Documentation expectations

A change is not documentation-complete when code and README disagree about maturity. Update all affected layers:

- README summary/claim language;
- canonical architecture docs;
- threat model / secure deployment when trust changes;
- operations runbook when failure/recovery changes;
- roadmap when a release gate is added, completed, or superseded;
- release notes for the exact release boundary.

Roadmap capabilities must not be written in present tense as implemented guarantees until evidence supports them.

## Security-sensitive changes

Changes to action authorization, approval binding, event hashing, canonical evidence, credential handling, executor isolation, tenant isolation, signatures/trust anchors, idempotency/recovery, or reconciliation require heightened review.

Once the project has more than one active maintainer, security-boundary changes should receive at least two maintainer approvals unless the documented emergency governance process says otherwise.

Do not expose production secrets or privileged credentials to untrusted fork workflows, model/runtime code, or test fixtures.

## Pull request description

For significant changes, describe:

- problem and scope;
- architecture/ADR impact;
- security/trust impact;
- compatibility/migration impact;
- tests/evidence added;
- documentation updated;
- known limitations/follow-up work.

## Developer certificate of origin

By contributing, you certify that you have the right to submit the contribution under the Apache License, Version 2.0. Sign commits with `git commit -s` when possible.
