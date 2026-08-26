# @prodkit/control-next

`@prodkit/control-next` provides server-side Next.js helpers for calling ProdKit Control and building guarded App Router route handlers.

## Maturity

**Supported first-party package.** It is designed for server use. It intentionally does not turn browser-supplied credentials, cookies, tenant identifiers, or proxy headers into control-plane authority.

## Start here

```ts
import { ControlServerClient } from "@prodkit/control-next";

const control = new ControlServerClient({
  baseUrl: "https://control.internal.example/",
  tenantId: "tenant-123",
  accessToken: async () => process.env.PRODKIT_CONTROL_TOKEN ?? null,
});

const result = await control.request("/v1/runs");
```

`createControlRouteHandler` can be used in an App Router `route.ts` file when a browser-facing application needs a narrow server-side bridge. The helper uses explicit method/header allowlists, refuses cross-origin path escape, requires HTTPS outside localhost, and does not relay browser authorization or cookie headers by default.

## Security boundary

Keep control-plane access tokens on the server. The helper is not a browser SDK and does not establish user authorization by itself; authentication, tenant derivation, policy, approval, and effect authorization remain server/control-plane responsibilities.

Licensed under Apache-2.0. See the package `LICENSE` and `NOTICE` files shipped with the distribution.
