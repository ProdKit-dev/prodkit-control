# `prodkit-reconcile-github`

`prodkit-reconcile-github` is the supported first-party reconciler for GitHub repository, workflow, review, and related external evidence used by ProdKit Control.

GitHub is replaceable and this package is optional when a deployment uses another source, but it is a supported implementation. Observation credentials should be read-only/least-privilege where possible and independent from mutation credentials. Evidence must be tied to exact repository/ref/run identities and freshness requirements.

Unexpected or conflicting GitHub activity is a finding, not an implicit authorization. Required unavailable/stale evidence must remain unavailable/stale under the selected assurance profile.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.