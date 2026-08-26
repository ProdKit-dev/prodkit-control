# @prodkit/control-react

`@prodkit/control-react` provides dependency-free external-store and mutation lifecycle primitives for React applications consuming ProdKit Control data.

## Maturity

**Supported first-party package.** React is deliberately not bundled or treated as a canonical dependency. The package exposes primitives compatible with React's `useSyncExternalStore` contract.

## Start here

```ts
import { createControlResource } from "@prodkit/control-react";

const runs = createControlResource(async (signal) => {
  const response = await fetch("/api/control/runs", { signal });
  if (!response.ok) throw new Error(`request failed: ${response.status}`);
  return response.json();
});
```

Pass `runs.subscribe` and `runs.getSnapshot` to your installed React version's `useSyncExternalStore`. `ControlResource` aborts stale loads and preserves explicit idle/loading/success/error state; `ControlMutation` provides a small mutation lifecycle primitive.

## Security boundary

This package manages client-side state only. Do not put production control-plane credentials in browser code. Route privileged requests through a trusted server boundary such as `@prodkit/control-next`, and keep authorization and tenant identity authoritative on the control plane.

Licensed under Apache-2.0. See the package `LICENSE` and `NOTICE` files shipped with the distribution.
