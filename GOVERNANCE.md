# Governance

ProdKit Control begins with a maintainer-led governance model and is intended to evolve toward a multi-maintainer, consensus-oriented project while preserving explicit security and architecture ownership.

## Roles

- **Contributor:** submits issues, documentation, tests, or code.
- **Reviewer:** has demonstrated domain expertise and may review pull requests.
- **Maintainer:** can merge changes, publish releases, participate in security response, and approve architecture decisions.

## Decision process

Routine changes use lazy consensus.

Changes that materially affect canonical contracts, trust/security boundaries, compatibility, mandatory dependencies, tenant isolation, credential ownership, uncertain-execution/idempotency semantics, signing/trust anchors, or the supported production/enterprise profile require an Architecture Decision Record and explicit maintainer approval.

See [`docs/architecture/decisions/README.md`](docs/architecture/decisions/README.md).

Accepted ADRs are immutable historical rationale. A later decision supersedes an earlier ADR rather than rewriting it.

## Security-sensitive decisions

Security-sensitive changes receive heightened review. Once more than one active maintainer exists, changes to authorization, approval binding, canonical integrity, credential/executor isolation, tenant isolation, signing/trust policy, durable recovery, or reconciliation should require at least two maintainer approvals except under a documented emergency process.

Emergency action must still leave complete audit/decision evidence and should receive post-event review.

## Release governance

A release is eligible only when its documented release gates are evidenced. Version numbers do not override the architecture/roadmap maturity model.

In particular:

- a fully closed release may still be a foundation milestone rather than a production/enterprise profile;
- roadmap capabilities are not considered implemented because a package/extension boundary exists;
- production/enterprise claim language must match [`docs/architecture/guarantees.md`](docs/architecture/guarantees.md);
- exact release boundaries belong in immutable release notes and release artifacts.

## Architecture stewardship

Maintainers are responsible for keeping these synchronized after accepted architecture changes:

- canonical architecture documentation;
- security threat model and secure deployment guidance;
- operations runbook;
- compatibility/migration guidance;
- README claim language;
- roadmap gates;
- release notes for affected versions.

## Independence

The canonical core must remain usable without a required commercial provider. Commercial and open-source integrations are welcome as replaceable adapters.

A new mandatory vendor/service dependency requires an ADR explaining why it does not undermine the project's provider-neutral and standalone-capable architecture, or explicitly changes that architectural promise.

## Conflicts of interest

Reviewers/maintainers should disclose material conflicts when a decision privileges a provider, commercial integration, or architecture in which they have a direct interest. The project should prefer technically justified replaceable interfaces over exclusive vendor coupling.

## Changes to governance

Material governance changes should be proposed transparently and reviewed like other high-impact project decisions. Governance changes must not silently weaken release, security, or architecture review gates.
