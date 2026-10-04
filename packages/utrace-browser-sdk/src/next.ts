"use client";

/**
 * `@utrace/browser-sdk/next` — Next.js App Router helper (Next 16, React 19).
 *
 * Activation is a bounded pre-application gate: it must complete before the
 * preview hydrates or sends client-side product requests. A browser cannot
 * suspend hydration for an asynchronous gate, so the strictest available
 * equivalent is used here — the gate starts during the first client module
 * evaluation, before any application client component renders, and
 * `UTraceGate` withholds the application subtree until the runtime is `ready`.
 * On failure it renders the failure surface and never renders the application
 * as an unobserved substitute.
 *
 * The runtime module is loaded through a dynamic import inside the preview
 * branch only, so a production build that pins `profile` to `"production"`
 * excludes the active runtime from its client bundle. Annotation helpers a
 * production build retains stay inert.
 */

import { createElement, useEffect, useState, type ReactElement, type ReactNode } from "react";
import type { BrowserSdkConfig } from "./core/config.js";
import type { RuntimeHealth } from "./runtime/readiness.js";
import { inertAnnotationSurface } from "./annotations/api.js";
import type { UTraceSession } from "./index.js";

export type PreviewProfile = "preview" | "production";

export type GateStatus =
  | { readonly phase: "activating" }
  | { readonly phase: "ready"; readonly session: UTraceSession }
  | { readonly phase: "failed"; readonly error: Error; readonly health: RuntimeHealth | null };

interface GateSingleton {
  readonly promise: Promise<UTraceSession>;
  status: GateStatus;
}

let singleton: GateSingleton | null = null;

/**
 * Starts the activation gate exactly once for this document. Calling it again
 * returns the same promise, so a rerender never opens a second runtime.
 */
export const startUTracePreview = (config: BrowserSdkConfig): Promise<UTraceSession> => {
  if (singleton !== null) return singleton.promise;
  const promise = (async (): Promise<UTraceSession> => {
    const { createUTraceBootstrap } = await import("./index.js");
    const bootstrap = createUTraceBootstrap(window, config);
    return bootstrap.activate();
  })();
  const created: GateSingleton = { promise, status: { phase: "activating" } };
  singleton = created;
  void promise.then(
    (session) => {
      created.status = { phase: "ready", session };
    },
    (cause: unknown) => {
      created.status = {
        phase: "failed",
        error: cause instanceof Error ? cause : new Error(String(cause)),
        health: null,
      };
    },
  );
  return promise;
};

/** Exposed for tests and for a server-rendered pass, where no gate exists yet. */
export const utracePreviewStatus = (): GateStatus => singleton?.status ?? { phase: "activating" };

export interface UTraceGateProps {
  readonly config: BrowserSdkConfig;
  readonly profile: PreviewProfile;
  readonly children: ReactNode;
  /** Rendered while the gate runs. The application subtree is not mounted yet. */
  readonly pending: ReactNode;
  /** Rendered when activation or readiness fails. The application never boots. */
  readonly renderFailure: (error: Error) => ReactNode;
}

/**
 * Gates the application subtree on a ready observation runtime.
 *
 * Place it as the outermost client component in the root layout of the preview
 * profile, wrapping everything the interview observes.
 */
export const UTraceGate = (props: UTraceGateProps): ReactElement => {
  const [status, setStatus] = useState<GateStatus>(() => {
    if (props.profile !== "preview") return { phase: "ready", session: inertSession() };
    // Starting here rather than in an effect keeps the gate ahead of every
    // application client component's first render.
    void startUTracePreview(props.config);
    return utracePreviewStatus();
  });

  useEffect(() => {
    if (props.profile !== "preview") return;
    let cancelled = false;
    void startUTracePreview(props.config).then(
      (session) => {
        if (!cancelled) setStatus({ phase: "ready", session });
      },
      (cause: unknown) => {
        if (cancelled) return;
        setStatus({
          phase: "failed",
          error: cause instanceof Error ? cause : new Error(String(cause)),
          health: null,
        });
      },
    );
    return (): void => {
      cancelled = true;
    };
  }, [props.profile, props.config]);

  if (status.phase === "ready") return createElement("div", { "data-utrace-gate": "ready" }, props.children);
  if (status.phase === "failed") {
    return createElement("div", { "data-utrace-gate": "failed" }, props.renderFailure(status.error));
  }
  return createElement("div", { "data-utrace-gate": "activating" }, props.pending);
};

/**
 * The production profile has no runtime. The session object exists so client
 * annotation call sites compile and stay inert.
 */
const inertSession = (): UTraceSession => ({
  annotations: inertAnnotationSurface(),
  grantWorkerCapability: (): never => {
    throw new Error("uTrace grants no worker capability in the production profile");
  },
  health: (): RuntimeHealth => {
    throw new Error("uTrace health is unavailable in the production profile");
  },
  pause: () => Promise.resolve(),
  resume: () => Promise.resolve(),
  terminate: () => undefined,
});
