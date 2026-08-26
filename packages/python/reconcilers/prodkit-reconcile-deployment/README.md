# `prodkit-reconcile-deployment`

`prodkit-reconcile-deployment` is the supported first-party boundary for reconciling controlled deployment records with an external deployment platform's observed state.

The package is optional when that evidence source is not selected, but the implementation is part of the supported first-party package set. Observations should bind immutable artifact/release identity, target/environment identity, provider operation identity, and observation time where available.

A claimed successful deployment is not sufficient evidence by itself. Missing, stale, conflicting, or mismatched required observations remain explicit reconciliation outcomes and must not be silently treated as success.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.