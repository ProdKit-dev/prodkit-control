/** Next.js server and route-handler helpers. Never expose server credentials to browser bundles. */
export const CONTROL_NEXT_PACKAGE_VERSION = "0.9.1" as const;

export type ControlAccessTokenProvider = () => Promise<string | null>;

export interface ControlServerClientOptions {
  readonly baseUrl: string;
  readonly tenantId: string;
  readonly accessToken?: ControlAccessTokenProvider;
  readonly fetch?: typeof globalThis.fetch;
}

export class ControlServerClient {
  readonly #baseUrl: URL;
  readonly #tenantId: string;
  readonly #accessToken: ControlAccessTokenProvider | null;
  readonly #fetch: typeof globalThis.fetch;

  constructor(options: ControlServerClientOptions) {
    const baseUrl = new URL(options.baseUrl);
    if (baseUrl.protocol !== "https:" && baseUrl.hostname !== "localhost") {
      throw new Error("ProdKit Control server baseUrl must use HTTPS outside localhost");
    }
    this.#baseUrl = baseUrl;
    this.#tenantId = requireNonBlank(options.tenantId, "tenantId");
    this.#accessToken = options.accessToken ?? null;
    this.#fetch = options.fetch ?? globalThis.fetch;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.raw(path, init);
    if (!response.ok) {
      throw new Error(`ProdKit Control request failed: ${response.status}`);
    }
    return (await response.json()) as T;
  }

  async raw(path: string, init: RequestInit = {}): Promise<Response> {
    const target = resolveControlPath(this.#baseUrl, path);
    const headers = new Headers(init.headers);
    headers.set("x-prodkit-tenant-id", this.#tenantId);
    if (init.body !== undefined && init.body !== null && !headers.has("content-type")) {
      headers.set("content-type", "application/json");
    }
    if (this.#accessToken !== null) {
      const token = await this.#accessToken();
      if (token !== null) {
        headers.set("authorization", `Bearer ${requireNonBlank(token, "access token")}`);
      }
    }
    return await this.#fetch(target, {
      ...init,
      headers,
      redirect: "manual",
    });
  }
}

export interface ControlRouteHandlerOptions extends ControlServerClientOptions {
  /** Map the incoming route request to a fixed control-plane path. */
  readonly resolvePath: (request: Request) => string;
  readonly allowedMethods?: ReadonlySet<string>;
  readonly forwardHeaders?: ReadonlySet<string>;
}

/**
 * Create a route handler suitable for a Next.js App Router `route.ts` file.
 *
 * Only an explicit method set and explicit header allowlist are forwarded. Cookies,
 * authorization headers and arbitrary host/proxy headers from the browser are never relayed.
 */
export function createControlRouteHandler(
  options: ControlRouteHandlerOptions,
): (request: Request) => Promise<Response> {
  const client = new ControlServerClient(options);
  const allowedMethods = options.allowedMethods ?? new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
  const forwardHeaders = options.forwardHeaders ?? new Set(["accept", "content-type", "if-match", "idempotency-key"]);

  return async (request: Request): Promise<Response> => {
    const method = request.method.toUpperCase();
    if (!allowedMethods.has(method)) {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: { allow: [...allowedMethods].join(", ") },
      });
    }
    const path = options.resolvePath(request);
    const headers = new Headers();
    for (const name of forwardHeaders) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    const body = method === "GET" || method === "HEAD" ? null : await request.arrayBuffer();
    const upstream = await client.raw(path, {
      method,
      headers,
      body,
    });
    const responseHeaders = new Headers();
    for (const name of ["content-type", "etag", "cache-control", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  };
}

function resolveControlPath(baseUrl: URL, path: string): URL {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
    throw new Error("ProdKit Control path must be a single-origin absolute path");
  }
  const target = new URL(path, baseUrl);
  if (target.origin !== baseUrl.origin) {
    throw new Error("ProdKit Control path escaped the configured origin");
  }
  return target;
}

function requireNonBlank(value: string, label: string): string {
  if (value.trim().length === 0) throw new Error(`${label} must be non-blank`);
  return value;
}
