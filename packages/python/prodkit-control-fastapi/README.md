# prodkit-control-fastapi

`prodkit-control-fastapi` exposes the ProdKit Control HTTP API without making HTTP transport the source of control-plane authority.

Production applications must inject an authenticated principal resolver. Header-based identity is disabled by default and exists only as an explicit development option. Tenant scope, actor identity, approval roles, lifecycle admission, and readiness are validated at the API boundary before requests reach the runtime.

The adapter composes `prodkit-control-core` contracts with `prodkit-control-runtime` services and preserves the same fail-closed policy, approval, execution, lineage, and evidence rules used by non-HTTP hosts.
