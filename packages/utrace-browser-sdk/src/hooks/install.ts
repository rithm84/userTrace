/**
 * Single-owner idempotent hook installation.
 *
 * Activation readiness proves that fetch, XHR, history, error, interaction, and
 * mutation capture are each installed exactly once. A second copy of the SDK in
 * the same document, or a second call into this installer, is a
 * `duplicate_runtime_or_hook` failure rather than a second observation path.
 */

import { UTraceRuntimeError } from "../core/failure.js";

export type HookName = "fetch" | "xhr" | "history" | "errors" | "interactions" | "mutations";

export const HOOK_NAMES: readonly HookName[] = ["fetch", "xhr", "history", "errors", "interactions", "mutations"];

export type HookStatus = Readonly<Record<HookName, boolean>>;

/** Cross-realm marker so a second bundle copy is detected, not silently layered. */
export const HOOK_OWNER_MARKER = Symbol.for("utrace.hook.owner");

interface Markable {
  [HOOK_OWNER_MARKER]?: string;
}

export const readOwner = (value: object): string | null => (value as Markable)[HOOK_OWNER_MARKER] ?? null;

export const markOwner = (value: object, runtimeId: string): void => {
  Object.defineProperty(value, HOOK_OWNER_MARKER, {
    value: runtimeId,
    enumerable: false,
    writable: false,
    configurable: true,
  });
};

/** Throws when another owner already patched the object this hook wraps. */
export const assertUnowned = (name: HookName, value: object, runtimeId: string): void => {
  const owner = readOwner(value);
  if (owner === null) return;
  throw new UTraceRuntimeError("duplicate_runtime_or_hook", `${name} capture is already installed in this document`, {
    hook: name,
    existing_owner: owner,
    requesting_owner: runtimeId,
  });
};

export type HookUninstall = () => void;

export class HookInstaller {
  readonly #runtimeId: string;
  readonly #installed = new Map<HookName, HookUninstall>();

  constructor(runtimeId: string) {
    this.#runtimeId = runtimeId;
  }

  get runtimeId(): string {
    return this.#runtimeId;
  }

  install(name: HookName, installer: (runtimeId: string) => HookUninstall): void {
    if (this.#installed.has(name)) {
      throw new UTraceRuntimeError("duplicate_runtime_or_hook", `${name} capture was installed twice`, {
        hook: name,
        runtime_id: this.#runtimeId,
      });
    }
    this.#installed.set(name, installer(this.#runtimeId));
  }

  /** Readiness proof: every required hook installed, each exactly once. */
  status(): HookStatus {
    return {
      fetch: this.#installed.has("fetch"),
      xhr: this.#installed.has("xhr"),
      history: this.#installed.has("history"),
      errors: this.#installed.has("errors"),
      interactions: this.#installed.has("interactions"),
      mutations: this.#installed.has("mutations"),
    };
  }

  allInstalled(): boolean {
    return HOOK_NAMES.every((name) => this.#installed.has(name));
  }

  uninstallAll(): void {
    for (const uninstall of this.#installed.values()) uninstall();
    this.#installed.clear();
  }
}
