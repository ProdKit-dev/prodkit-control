# @prodkit/control-client

`@prodkit/control-client` is the minimal TypeScript HTTP client for the ProdKit Control API.

## Maturity

**Supported first-party package.** It is an API transport helper, not an authorization authority and not a replacement for the server-side security boundary.

## Start here

```ts
import { ControlClient } from "@prodkit/control-client";

const client = new ControlClient({
  baseUrl: "http://localhost:8000/",
  tenantId: "example-tenant",
});

const health = await client.request<{ status: string }>("/healthz");
```

For production authentication, use a server-side integration that supplies the deployment's authenticated credential mechanism; do not treat the tenant header alone as production identity. `@prodkit/control-next` provides a guarded server/route-handler integration for Next.js.

## Security boundary

The client sends the configured tenant header and delegates HTTP execution to `fetch`. The server remains responsible for authenticating the principal, deriving trusted tenant context, authorizing operations, enforcing idempotency, and failing closed when required controls are unavailable.

Licensed under Apache-2.0. See the package `LICENSE` and `NOTICE` files shipped with the distribution.
