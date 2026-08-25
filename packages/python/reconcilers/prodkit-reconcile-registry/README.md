# `prodkit-reconcile-registry`

`prodkit-reconcile-registry` is the supported first-party boundary for reconciling claimed build/artifact identity with external registry evidence.

It is optional when a deployment does not select a registry source, but is a supported implementation. Prefer immutable artifact digests over mutable tags, use least-privilege observation credentials, and bind evidence to the exact registry/repository/platform identity required by policy.

A tag name alone is not strong provenance. Missing, stale, unavailable, or conflicting required registry evidence must remain explicit and may block assurance under strict profiles.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.