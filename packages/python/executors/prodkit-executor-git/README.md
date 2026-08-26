# `prodkit-executor-git`

`prodkit-executor-git` is the supported first-party executor for controlled Git repository effects.

It is an optional runtime dependency, not a mandatory authority for the canonical core. Use it when an authorized action must interact with an explicitly configured repository. Repository scope, operation scope, expected state, credentials, and retry/idempotency behavior must be controlled by the deployment rather than by model-supplied arguments.

Git history is evidence/witness state, not the complete ProdKit Control source of truth. Production credentials should be short-lived or brokered and must not be exposed to untrusted model/runtime code.

Start with this package's executor exports and the repository action/executor documentation. Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.