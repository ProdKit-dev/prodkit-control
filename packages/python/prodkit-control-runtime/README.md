# prodkit-control-runtime

`prodkit-control-runtime` implements the provider-neutral coordination layer for ProdKit Control.

It provides the action broker, policy and approval orchestration, credential-lease boundaries, execution-attempt handling, evidence and attestation flows, lineage coordination, governance, tenancy, reconciliation, recovery, high-availability primitives, and operational security controls. It depends on `prodkit-control-core` for canonical contracts while keeping providers and external systems behind explicit interfaces.

The runtime is fail-closed around authorization, approval, credential scope, ambiguous external effects, tenant boundaries, and evidence verification. Durable deployments can replace in-memory adapters with supported persistence and provider integrations without changing the canonical control semantics.
