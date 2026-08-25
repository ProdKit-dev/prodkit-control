# ProdKit Control contract authority

ProdKit Control's portable semantics are **language-neutral**. Python and TypeScript are native implementations of the contract; neither implementation is the normative source of truth.

## Normative authority

Portable behavior is defined by the versioned artifacts in this repository:

1. semantic specifications under `contracts/specifications/`;
2. published schemas under `schemas/`;
3. protocol-family definitions under `contracts/protocols/`;
4. canonicalization profiles;
5. golden and adversarial conformance vectors under `contracts/conformance/`.

`contracts/index.json` is the authority index. It names the currently supported portable profiles and the native runtimes that must conform to them.

Implementation code under `packages/python/` and `packages/typescript/` may provide validation, APIs, performance optimizations, adapters, or generated representations, but it may not silently redefine portable semantics.

## Policy boundary

ProdKit Control defines a portable policy-decision contract and portable composition semantics. Organization policy engines such as OPA, Permit, Cedar-style systems, cloud IAM, or future providers remain replaceable adapters. An adapter may evaluate organization-owned policy, but its result must normalize into the canonical decision contract and cannot weaken Control's fail-closed composition rules.

The built-in reference policy is itself specified as a portable profile and is covered by cross-runtime conformance vectors. It is not authoritative merely because one implementation happens to be written in Python.

## Change rule

A security-critical or portable semantic change is incomplete unless the same change updates, as applicable:

- the language-neutral specification;
- the published schema/protocol identifier;
- conformance vectors;
- every native runtime that claims that profile;
- compatibility/migration documentation when the change is not backward compatible.

CI must execute conformance independently through both supported native runtimes. A runtime-specific test suite is necessary but not sufficient evidence of portable semantic compatibility.

## Non-portable implementation details

Runtime-specific framework adapters, subprocess mechanics, HTTP clients, SDK ergonomics, database drivers, browser APIs, and deployment integrations may remain language-specific when their behavior is not declared portable. They still project into canonical language-neutral records at the trust boundary.
