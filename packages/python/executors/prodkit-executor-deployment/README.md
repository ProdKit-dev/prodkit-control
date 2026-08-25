# `prodkit-executor-deployment`

Provider-neutral deployment executor for controlled production rollout effects.

The executor requires explicit environment, resource, and operation allowlists; `deploy` and `promote` operations are bound to immutable `sha256:<digest>` artifacts. Every external effect requires a short-lived credential lease, reuses the canonical action idempotency key, and returns a digest-bound state observation. Provider-specific deployment systems are injected through the `DeploymentTransport` protocol rather than becoming architectural dependencies.
