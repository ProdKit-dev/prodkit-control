# `prodkit-reconcile-database`

`prodkit-reconcile-database` is the supported first-party boundary for comparing claimed database/control-plane effects with independently observed database state or audit evidence.

It is optional when a deployment does not select database reconciliation, but it is not a scaffold. Read credentials should be separated from mutation authority where possible, queries must be bounded, and tenant/environment/resource scope must be explicit.

Required evidence that is stale, unavailable, conflicting, or mismatched fails closed according to the active reconciliation/assurance profile. The reconciler does not retroactively authorize unexplained external changes.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.