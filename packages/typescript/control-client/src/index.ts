export interface ControlClientOptions {
  readonly baseUrl: string;
  readonly tenantId: string;
  readonly fetch?: typeof globalThis.fetch;
}

export class ControlClient {
  readonly #baseUrl: URL;
  readonly #tenantId: string;
  readonly #fetch: typeof globalThis.fetch;

  constructor(options: ControlClientOptions) {
    this.#baseUrl = new URL(options.baseUrl);
    this.#tenantId = options.tenantId;
    this.#fetch = options.fetch ?? globalThis.fetch;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.#fetch(new URL(path, this.#baseUrl), {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-prodkit-tenant-id": this.#tenantId,
        ...init.headers,
      },
    });
    if (!response.ok) throw new Error(`ProdKit request failed: ${response.status}`);
    return (await response.json()) as T;
  }
}
