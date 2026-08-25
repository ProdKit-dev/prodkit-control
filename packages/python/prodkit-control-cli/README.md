# prodkit-control-cli

`prodkit-control-cli` provides operator and local-development commands for ProdKit Control, including offline evidence verification and supported control-plane operations.

The CLI is an adapter over canonical contracts and runtime services; it is not an authorization bypass. Commands that reach controlled effects remain subject to the same policy, approval, credential, tenant, idempotency, execution-attempt, and evidence invariants as other hosts.

Use it for reproducible local workflows, diagnostics, and verification while keeping production credentials and provider authority behind the configured runtime boundaries.
