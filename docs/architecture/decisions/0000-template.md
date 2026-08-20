# ADR 0000: Short decision title

- Status: proposed
- Date: YYYY-MM-DD
- Owners: maintainers / responsible domain
- Supersedes: none
- Superseded by: none

## Context

Describe the problem, current behavior, constraints, and why this decision is needed now.

## Decision drivers

- Security/trust requirements
- Reliability/recovery requirements
- Provider/vendor neutrality
- Compatibility/migration impact
- Operability and deployment complexity
- Performance/scale requirements
- Evidence/assurance requirements

Replace or extend this list as appropriate.

## Decision

State the chosen architecture clearly enough that implementation and review can test whether a change conforms to it.

## Alternatives considered

### Alternative A

Describe the alternative, benefits, drawbacks, and why it was not selected.

### Alternative B

Describe the alternative, benefits, drawbacks, and why it was not selected.

## Security and trust consequences

Explain effects on:

- trust boundaries;
- identity/authorization;
- credential ownership;
- tenant isolation;
- tamper evidence/trust anchors;
- bypass/reconciliation risk.

## Reliability and recovery consequences

Explain effects on:

- idempotency;
- crash/retry semantics;
- uncertain external effects;
- HA/failover;
- backup/restore/DR.

## Compatibility and migration

Document:

- schema/API/adapter compatibility impact;
- data migration requirements;
- rollout/rollback constraints;
- deprecation or support implications.

## Operational consequences

Document new dependencies, observability, capacity, runbook, incident, and deployment requirements.

## Evidence and validation

List the tests, proofs, exercises, benchmarks, or independent review needed before the decision can support production/enterprise claims.

## Documentation updates

List canonical docs that must be updated after acceptance, such as:

- `docs/architecture/overview.md`
- specialized architecture documents
- threat model / secure deployment / runbook
- `README.md`
- `ROADMAP.md`

## Follow-up work

- [ ] Implementation task
- [ ] Tests/evidence
- [ ] Documentation sync
- [ ] Migration/operations work
