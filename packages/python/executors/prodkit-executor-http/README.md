# `prodkit-executor-http`

`prodkit-executor-http` is the supported first-party executor for explicitly allowlisted HTTP effects.

It is an optional runtime dependency and does not make arbitrary network access a supported capability. Production deployments must restrict destinations, schemes, methods, request size, redirects, timeouts, credentials, and response/evidence bounds. Model- or client-supplied URLs must not silently bypass administrator-owned target policy.

Keep production secrets inside the executor/credential boundary and apply default-deny egress where the deployment profile requires it. Ambiguous external effects must follow the normal uncertain-execution and reconciliation rules rather than being blindly retried.

Start with the package exports and secure deployment documentation. Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.