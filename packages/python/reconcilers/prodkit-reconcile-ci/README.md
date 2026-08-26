# `prodkit-reconcile-ci`

`prodkit-reconcile-ci` is the supported first-party boundary for reconciling claimed CI/build state with external CI evidence.

The reconciler is optional to deployments that do not use this source, but it is a supported implementation in the v0.9 package set. External CI data remains witness evidence rather than canonical history. Missing, stale, unavailable, or conflicting required evidence must not be converted into success.

Use a separately authenticated read path where practical and bind observations to exact repository/source/build identities. Reconciliation should surface mismatches and unexplained activity rather than synthesizing authorization after the fact.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.