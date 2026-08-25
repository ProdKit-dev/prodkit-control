# `prodkit-provider-generic`

`prodkit-provider-generic` is the optional-supported provider-neutral model boundary for integrating a caller-supplied model transport without making any model vendor authoritative.

The adapter normalizes provider input/output into ProdKit Control's provider boundary while leaving authorization, policy, approval, execution, evidence, and reconciliation in the control plane. A model response or tool-call proposal never acquires production authority merely because it passed through this provider.

Use this package when a vendor-specific provider package is unnecessary or when embedding a custom transport. Keep provider credentials outside canonical action/evidence payloads and apply your deployment's timeout, data-handling, and network controls.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.