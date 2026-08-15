# Product lineage model

## Principle

Code may be disposable as an implementation, but it cannot be anonymous as evidence. ProdKit
therefore treats an exact source tree, verification result, build artifact, deployment, and
production observation as durable content-addressed product identities even when their underlying
implementations are later regenerated or discarded.

## Canonical graph

`LineageGraph` is scoped to one tenant and run. Every node has a UUID, SHA-256 content identity,
recording time, optional evidence artifacts, and a stage-specific typed payload. Supported node
kinds are:

- specification revision;
- decision set;
- generator configuration and inputs;
- source tree;
- verification result;
- build artifact;
- authorization;
- agent action;
- deployment;
- production observation;
- reconciliation.

Relations are typed subject-predicate-object assertions and endpoint constrained. For example,
`generator_configuration generated_from specification_revision`, while a `built_as` edge can only
connect a source tree to a build artifact. Relation references repeat the endpoint kind, UUID, and digest so
stale or substituted identities fail validation. Graph validation also rejects missing endpoints,
duplicate identities, duplicate edges, cross-run or cross-tenant nodes, and cycles.

## Production completeness

The reference `ProductionLineagePolicy` accepts a production observation only when it can trace a
continuous graph containing:

1. an approved specification revision and decision set;
2. a successful generator configuration and exact source tree;
3. passing verification against content-addressed requirements and results;
4. a successful build artifact;
5. affirmative authorization bound to policy, approval, and action-set digests;
6. a successful controlled agent action and deployment;
7. an observed production-state digest;
8. matched independent reconciliation.

Assessment returns every satisfied and missing requirement. Enforcement raises
`IncompleteLineageError` and fails closed when any required stage is missing or unsuccessful.
Organizations can implement stricter policies against the same canonical graph.

## Ownership boundary

ProdKit does not need to author specifications, generate code, run CI, build artifacts, deploy
software, or operate observability backends. Those systems remain authoritative witnesses for
their own operations. ProdKit owns their canonical linkage, authorization, integrity validation,
and reconciliation into a portable evidence record.

Git is therefore one witness in the chain, not the canonical explanation of the running system.
Agent reasoning can be retained as optional evidence, but proposals, decisions, actions, effects,
and the typed relationships between their identities are mandatory evidence.
