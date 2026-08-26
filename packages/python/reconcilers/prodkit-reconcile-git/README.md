# `prodkit-reconcile-git`

`prodkit-reconcile-git` is the supported first-party reconciler for comparing claimed source/repository state with independently read Git state.

It is an optional evidence adapter, but a supported implementation. Git is one witness in the lineage chain rather than the canonical explanation of production. Reconciliation should bind exact commit/tree identities and surface missing, divergent, stale, or unexpected state.

Use read-only or least-privilege observation credentials where practical. The reconciler must not rewrite history or manufacture authorization to make an unexplained repository state appear compliant.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.