# ProdKit Control contributor guidance

## Product boundary

ProdKit Control is a provider-neutral intent-to-production control and assurance plane. It owns
canonical linkage, authorization, integrity, evidence portability, and reconciliation across
specification, generation, verification, build, deployment, and production observation. It does
not need to replace the systems that perform those functions.

Preserve this invariant:

> No production state is acceptable unless it is traceable to an approved specification revision
> through a continuous, independently verifiable provenance chain.

Git, CI, deployment platforms, model providers, and observability systems are witnesses or
projections. They are not the canonical explanation of the running product.

## Engineering bar

- Build advanced, complete, production-oriented, general-purpose capabilities.
- Keep core contracts provider-neutral and usable standalone.
- Fail closed on missing authorization, integrity failures, tenant mismatches, and incomplete
  production lineage.
- Prefer immutable, versioned, content-addressed records and typed relationships.
- Treat generated code and tests as replaceable implementations but durable evidence identities.
- Keep model reasoning optional; proposals, decisions, actions, effects, and evidence mandatory.
- Include tests, schemas, documentation, operational behavior, and migration impact with changes.
- Do not claim production guarantees that depend on deployment controls not implemented here.

## Naming

- Product and repository: `prodkit-control` / ProdKit Control.
- Foundational Python packages: `prodkit-control-*`; modules: `prodkit_control_*`.
- TypeScript packages: `@prodkit/control*`.
- Keep `execution` only for the concrete act of executing an authorized side effect, such as
  `ExecutionResult`, executor contracts, and execution lifecycle events.
