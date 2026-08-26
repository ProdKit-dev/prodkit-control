# Examples

The examples in this directory are executable public API examples, not release-gate substitutes.

## `basic_dry_run.py`

Run:

```bash
uv sync --all-packages --group dev --locked
uv run python examples/basic_dry_run.py
```

The example creates a tenant-scoped run, constructs an exact `ActionSpec`, routes it through the policy/action-broker/executor boundary, and prints the resulting verification and observation identity. It uses only in-memory/reference components and the `DryRunExecutor`, so it is safe for local evaluation and does not mutate an external production system.

For the fuller evidence and lineage demonstration, run:

```bash
uv run prodkit-control demo --output .artifacts/demo
```

That CLI demo additionally constructs and enforces a complete lineage graph, exports a tenant-bound evidence bundle, and verifies the bundle before returning success.

## Production boundary

Do not translate an example's local convenience into production authority. Production deployments must provide authenticated principals, durable stores, explicit policy/approval integration, isolated executors, short-lived credentials, target allowlists, and independent observation/reconciliation appropriate to the selected assurance profile.

Read [`../docs/getting-started.md`](../docs/getting-started.md), [`../docs/security/secure-deployment.md`](../docs/security/secure-deployment.md), and [`../docs/architecture/guarantees.md`](../docs/architecture/guarantees.md) before enabling privileged effects.
