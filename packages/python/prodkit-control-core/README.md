# prodkit-control-core

`prodkit-control-core` defines the canonical, provider-neutral contracts shared by ProdKit Control.

It owns typed action, policy, approval, execution, evidence, lineage, governance, tenancy, recovery, security, and reliability models together with deterministic canonicalization and content-addressing helpers. The package does not perform external side effects and does not make any external provider authoritative.

The portable specifications under the repository-level `contracts/` directory are the language-neutral semantic authority. Python models in this package and the TypeScript contract surface are independent implementations that must satisfy the same conformance vectors.

Production consumers should treat contract validation and digest stability as trust boundaries: malformed or semantically incompatible state fails closed rather than being silently coerced.
