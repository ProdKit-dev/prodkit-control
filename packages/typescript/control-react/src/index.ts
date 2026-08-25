/** React external-store primitives for ProdKit Control data and mutation lifecycles. */
export const CONTROL_REACT_PACKAGE_VERSION = "0.9.1" as const;

export type ControlResourceStatus = "idle" | "loading" | "success" | "error";

export interface ControlResourceSnapshot<T> {
  readonly status: ControlResourceStatus;
  readonly data: T | null;
  readonly error: Error | null;
  readonly updatedAt: number | null;
}

export type ControlResourceLoader<T> = (signal: AbortSignal) => Promise<T>;
export type ControlResourceListener = () => void;

/**
 * A dependency-free external store designed for React `useSyncExternalStore`.
 * The package intentionally does not bundle React; consumers pass `resource.subscribe`
 * and `resource.getSnapshot` directly to their installed React version.
 */
export class ControlResource<T> {
  readonly #loader: ControlResourceLoader<T>;
  readonly #listeners = new Set<ControlResourceListener>();
  #snapshot: ControlResourceSnapshot<T> = {
    status: "idle",
    data: null,
    error: null,
    updatedAt: null,
  };
  #generation = 0;
  #abort: AbortController | null = null;

  constructor(loader: ControlResourceLoader<T>) {
    this.#loader = loader;
  }

  readonly subscribe = (listener: ControlResourceListener): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  readonly getSnapshot = (): ControlResourceSnapshot<T> => this.#snapshot;

  async load(): Promise<T> {
    const generation = ++this.#generation;
    this.#abort?.abort();
    const controller = new AbortController();
    this.#abort = controller;
    this.#setSnapshot({
      status: "loading",
      data: this.#snapshot.data,
      error: null,
      updatedAt: this.#snapshot.updatedAt,
    });
    try {
      const data = await this.#loader(controller.signal);
      if (generation !== this.#generation) return data;
      this.#setSnapshot({
        status: "success",
        data,
        error: null,
        updatedAt: Date.now(),
      });
      return data;
    } catch (error: unknown) {
      if (generation !== this.#generation) throw error;
      const normalized = error instanceof Error ? error : new Error(String(error));
      this.#setSnapshot({
        status: "error",
        data: this.#snapshot.data,
        error: normalized,
        updatedAt: this.#snapshot.updatedAt,
      });
      throw normalized;
    } finally {
      if (generation === this.#generation) this.#abort = null;
    }
  }

  setData(data: T): void {
    ++this.#generation;
    this.#abort?.abort();
    this.#abort = null;
    this.#setSnapshot({
      status: "success",
      data,
      error: null,
      updatedAt: Date.now(),
    });
  }

  reset(): void {
    ++this.#generation;
    this.#abort?.abort();
    this.#abort = null;
    this.#setSnapshot({
      status: "idle",
      data: null,
      error: null,
      updatedAt: null,
    });
  }

  dispose(): void {
    ++this.#generation;
    this.#abort?.abort();
    this.#abort = null;
    this.#listeners.clear();
  }

  #setSnapshot(snapshot: ControlResourceSnapshot<T>): void {
    this.#snapshot = snapshot;
    for (const listener of this.#listeners) listener();
  }
}

export function createControlResource<T>(loader: ControlResourceLoader<T>): ControlResource<T> {
  return new ControlResource(loader);
}

export interface ControlMutationSnapshot<TOutput> {
  readonly pending: boolean;
  readonly data: TOutput | null;
  readonly error: Error | null;
}

export type ControlMutationExecutor<TInput, TOutput> = (input: TInput) => Promise<TOutput>;

export class ControlMutation<TInput, TOutput> {
  readonly #execute: ControlMutationExecutor<TInput, TOutput>;
  readonly #listeners = new Set<ControlResourceListener>();
  #snapshot: ControlMutationSnapshot<TOutput> = { pending: false, data: null, error: null };
  #generation = 0;

  constructor(execute: ControlMutationExecutor<TInput, TOutput>) {
    this.#execute = execute;
  }

  readonly subscribe = (listener: ControlResourceListener): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  readonly getSnapshot = (): ControlMutationSnapshot<TOutput> => this.#snapshot;

  async mutate(input: TInput): Promise<TOutput> {
    const generation = ++this.#generation;
    this.#setSnapshot({ pending: true, data: this.#snapshot.data, error: null });
    try {
      const data = await this.#execute(input);
      if (generation === this.#generation) {
        this.#setSnapshot({ pending: false, data, error: null });
      }
      return data;
    } catch (error: unknown) {
      const normalized = error instanceof Error ? error : new Error(String(error));
      if (generation === this.#generation) {
        this.#setSnapshot({ pending: false, data: null, error: normalized });
      }
      throw normalized;
    }
  }

  reset(): void {
    ++this.#generation;
    this.#setSnapshot({ pending: false, data: null, error: null });
  }

  #setSnapshot(snapshot: ControlMutationSnapshot<TOutput>): void {
    this.#snapshot = snapshot;
    for (const listener of this.#listeners) listener();
  }
}

export function createControlMutation<TInput, TOutput>(
  execute: ControlMutationExecutor<TInput, TOutput>,
): ControlMutation<TInput, TOutput> {
  return new ControlMutation(execute);
}
