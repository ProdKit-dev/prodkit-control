# Contributing

Thank you for contributing to ProdKit Control.

ProdKit Control is a security-sensitive control-plane project. Contributions are welcome, but changes that affect authorization, evidence, execution, tenancy, compatibility, or production claims must include the tests and documentation needed to preserve the repository's fail-closed guarantees.

## Development setup

Requirements:

- Python 3.12 or newer;
- `uv`;
- Node.js 22 or newer and Corepack/pnpm 10 for TypeScript work;
- Docker only when running PostgreSQL/container integration paths.

From a source checkout:

```bash
make install
make check
```

`make install` uses the committed Python and Node lockfiles. `make check` runs the repository's local release/version checks, package-completeness and public-readiness contracts, language-neutral conformance, lint/type checking, tests, schema drift checks, TypeScript build/conformance checks, and local first-run smoke tests.

For a minimal sanity check before a larger setup:

```bash
uv sync --all-packages --group dev --locked
uv run prodkit-control demo --output .artifacts/demo
```

Pull requests from forks run the public CI path on GitHub-hosted runners. Same-repository qualification can use the configured trusted runner. Reusable governance workflows are pinned to immutable commits in the public `ProdKit-dev/prodkit-workflows` repository; contributors do not need access to private CI infrastructure to run fork PR checks.

## Development process

1. Open an issue for significant behavior, contract, trust-boundary, or deployment-profile changes.
2. Add or update an Architecture Decision Record (ADR) when the change meets the criteria in [`docs/architecture/decisions/README.md`](docs/architecture/decisions/README.md).
3. Keep provider- and vendor-specific behavior behind explicit adapter boundaries; do not move vendor authority into the canonical core.
4. Keep portable semantics language-neutral. Python and TypeScript implementations must conform to the versioned specifications/vectors under [`contracts/`](contracts/); neither native runtime becomes semantic authority.
5. Update canonical architecture/security/operations documentation when implementation changes an invariant, guarantee, failure mode, supported profile, or public usage boundary.
6. Add deterministic tests for lifecycle transitions and failure modes introduced or changed by the contribution.
7. Add integration/adversarial/migration tests when the change affects external effects, durable recovery, tenancy, credentials, signing, reconciliation, or compatibility.
8. Run `make check` before submitting a pull request.

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
- portable semantics come from language-neutral specifications/conformance rather than Python/TypeScript implementation accidents;
- package existence does not imply production maturity.

Start with [`docs/architecture/README.md`](docs/architecture/README.md) and [`docs/architecture/overview.md`](docs/architecture/overview.md).

## ADR process

Use [`docs/architecture/decisions/0000-template.md`](docs/architecture/decisions/0000-template.md) for changes that materially affect:

- canonical source-of-truth ownership;
- event/lineage/action/evidence semantics;
- language-neutral portable semantics or canonicalization;
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
- Portable semantic profiles are versioned and tested with shared conformance vectors.
- Existing fields are not repurposed to mean something materially different.
- Breaking contract changes require an appropriate new schema/API major version.
- Generated JSON Schemas must be committed and drift-checked in CI.
- Events are immutable after append; corrections are represented by new events.
- Lineage identities and relations are immutable historical assertions; corrections add superseding assertions/events rather than rewriting history.
- Security-critical readers reject unsupported semantics rather than silently guessing.
- Pre-1.0 compatibility may evolve, but breaking changes still require explicit release notes and migration impact.
- The 1.0 production profile must publish a supported compatibility, migration, and deprecation policy.

## Testing expectations

### Core/contract changes

Add deterministic unit/property tests for validation, canonicalization, hashing, state transitions, cross-runtime conformance, and negative cases.

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

Add migration/upgrade tests and recovery behavior for in-flight/uncertain actions where relevant. Published migration history is immutable; add a new migration rather than editing a shipped migration.

### Public/distribution changes

Update the public-readiness contract and smoke checks when a change affects installation, CLI/API entrypoints, package metadata, supported distribution channels, examples, support/security guidance, or end-user documentation.

## Documentation expectations

A change is not documentation-complete when code and current-facing docs disagree about maturity. Update all affected layers:

- README current-release summary/claim language;
- getting-started/examples when user behavior changes;
- canonical architecture docs;
- threat model / secure deployment when trust changes;
- operations runbook when failure/recovery changes;
- roadmap when a release gate is added, completed, or superseded;
- release notes for the exact release boundary.

Historical release notes and accepted ADRs remain historical evidence and must not be rewritten merely to make the current release look cleaner.

Roadmap capabilities must not be written in present tense as implemented guarantees until evidence supports them.

## Security-sensitive changes

Changes to action authorization, approval binding, event hashing, canonical evidence, credential handling, executor isolation, tenant isolation, signatures/trust anchors, idempotency/recovery, or reconciliation require heightened review.

Once the project has more than one active maintainer, security-boundary changes should receive at least two maintainer approvals unless the documented emergency governance process says otherwise.

Do not expose production secrets or privileged credentials to untrusted fork workflows, model/runtime code, examples, logs, or test fixtures.

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
