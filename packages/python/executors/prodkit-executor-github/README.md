# `prodkit-executor-github`

`prodkit-executor-github` is the supported first-party executor for bounded GitHub effects performed through ProdKit Control.

The package is an optional runtime dependency: GitHub is replaceable and is never the canonical authority for ProdKit Control semantics. Deployments must constrain repositories, operations, identities, credentials, and expected state independently of model proposals. Authorization must bind the exact action before a privileged GitHub effect is executed.

Use least-privilege installation/workload credentials and keep them inside the privileged executor boundary. GitHub API responses and audit data are external evidence and may be independently reconciled; a tool-call trace alone is not proof of the resulting state.

Start with the package exports and the repository executor/reconciliation documentation. Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.