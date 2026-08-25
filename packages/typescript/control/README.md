# @prodkit/control

`@prodkit/control` is the TypeScript implementation of ProdKit Control's portable contracts and deterministic semantics.

## Maturity

**Supported first-party package.** It is usable independently and does not make TypeScript the normative authority. Portable meaning is defined by the repository-level language-neutral specifications, schemas, canonicalization profiles, protocols, and shared conformance vectors.

## What it provides

The package exports typed contracts for actions, actors, policy and approval outcomes, lineage, evidence, tenancy, governance, reliability, recovery, and related control-plane records. It is intended for TypeScript services and applications that need to create, validate, exchange, or project ProdKit Control data without depending on the Python runtime.

## Start here

```ts
import type { ActionSpec, LineageGraph } from "@prodkit/control";
```

Use the language-neutral contract documentation in `contracts/` for wire semantics and the repository schemas for external validation. Do not infer authority from TypeScript structural typing alone at a production trust boundary.

## Security boundary

Types improve developer correctness but do not authenticate principals, authorize actions, verify signatures, isolate executors, or establish external truth. Production callers must use the control-plane authentication, policy, approval, execution, and reconciliation boundaries appropriate to their deployment profile.

Licensed under Apache-2.0. See the package `LICENSE` and `NOTICE` files shipped with the distribution.
