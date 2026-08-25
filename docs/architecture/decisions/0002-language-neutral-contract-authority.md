# ADR 0002: Language-neutral portable contract authority

- Status: Accepted
- Date: 2026-08-25
- Decision owners: ProdKit Control maintainers

## Context

ProdKit Control already exposes substantial Python and TypeScript surfaces. Security-critical semantics such as canonical content identity, policy outcomes, lineage meaning, and protocol behavior must remain portable across runtimes and future implementations.

If one implementation language becomes the implicit source of truth, other runtimes can only imitate implementation details. That creates hidden coupling, makes cross-language drift difficult to detect, and weakens provider-neutral/standalone claims. It is especially inappropriate for an execution and assurance control plane whose evidence may need to be verified independently of the original implementation stack.

ProdKit Quality independently adopted the same architectural principle for its portable quality semantics. ProdKit Control needs an explicit equivalent decision appropriate to its own control/provenance domain.

## Decision

Portable ProdKit Control semantics are language-neutral.

Normative authority consists of versioned semantic specifications, published schemas, protocol-family definitions, canonicalization profiles, and shared conformance vectors. `contracts/index.json` indexes the supported authority surface.

Python and TypeScript are native implementations. Neither runtime is normative merely because a class, model, evaluator, or generator was implemented there first.

The built-in reference policy and fail-closed policy composition are promoted into portable profiles. External policy systems remain replaceable adapters that normalize decisions into the canonical Control policy contract.

CI independently executes shared conformance vectors through each supported native runtime. Runtime-specific tests do not substitute for cross-runtime conformance.

## Consequences

### Positive

- Python and TypeScript can evolve independently without semantic parent/child coupling;
- future Go, Rust, Java, or other implementations can target stable portable contracts;
- policy, evidence, and provenance records remain independently interpretable;
- external policy engines remain integrations rather than hidden semantic authorities;
- runtime drift becomes a release-blocking failure rather than a documentation problem;
- generated schemas may use any tooling language without making that language normative.

### Costs

- portable semantic changes require specification/vector maintenance in addition to implementation work;
- both native runtimes must be updated before a shared profile can advance;
- some host-language conveniences are outside the portable profile unless explicitly specified;
- cross-runtime canonicalization must use a deliberately bounded value domain to avoid silent numeric/string ordering differences.

## Alternatives considered

### Python as canonical authority

Rejected. Python may remain the strongest implementation for some server/runtime capabilities, but implementation precedence is not a sound portable contract model.

### TypeScript as canonical authority

Rejected for the same reason. Browser/Node ecosystem fit does not make TypeScript the correct semantic parent for server or future native runtimes.

### Generate every runtime from one implementation AST

Rejected as the authority model. Code generation can reduce duplication, but the generator implementation would still become an accidental source of truth and could hide semantic mistakes across all generated outputs. Generated artifacts must instead conform to independently specified contracts.

### Require one external policy language such as Rego

Rejected. OPA/Rego and other policy systems are useful adapters, but making one provider/language mandatory would violate provider-neutral and standalone architecture. ProdKit owns normalized decision and composition semantics, not every organization's policy authoring language.

## Compatibility and migration

This decision preserves existing wire identifiers and published schemas. Existing Python contracts are not rewritten merely to relocate authority. Instead, the v0.9 contract authority references current published schemas and introduces language-neutral specifications/conformance around them.

A later incompatible semantic change must use the normal versioning/compatibility process rather than silently changing a native implementation.

## Verification

The v0.9 release must prove:

- `contracts/index.json` declares language-neutral authority and does not name runtime package roots as normative sources;
- portable canonicalization vectors pass independently in Python and TypeScript;
- built-in/default and conjunctive policy vectors pass independently in Python and TypeScript;
- CI runs those gates on every exact release candidate;
- the v0.9 roadmap explicitly blocks release on cross-runtime semantic drift;
- v0.10 and later inherit the same gate.
