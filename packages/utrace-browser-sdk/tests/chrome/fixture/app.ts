/**
 * The static test application.
 *
 * It is a client product, not part of the SDK: it installs the inert bootstrap,
 * activates it, and exposes a small control surface on `window` so the Chrome
 * tests can drive real user actions and read the runtime's public health
 * record. It also plays the extension's main-world bridge, opening the channel
 * and exchanging the exact `bridge-frame.v1` frames the real extension sends.
 */

import { createUTraceBootstrap, type RuntimeHealth, type UTraceSession } from "../../../src/index.js";

interface BridgeReply {
  readonly kind: string;
  readonly body: Record<string, unknown>;
}

/** The extension protocol's frame envelope, as the extension writes it. */
interface BridgeFrame {
  readonly schema_version: 1;
  readonly kind: string;
  readonly phase: string;
  readonly exchange_id: string;
  readonly payload: Record<string, unknown>;
}

interface TestSurface {
  readonly ready: Promise<void>;
  readonly health: () => RuntimeHealth;
  readonly activationError: () => string | null;
  readonly activateAgain: () => Promise<string>;
  readonly bridge: {
    readonly connect: () => Promise<BridgeReply>;
    readonly send: (
      kind: string,
      phase: string,
      payload: Record<string, unknown>,
    ) => Promise<BridgeReply>;
    readonly notify: (payload: Record<string, unknown>) => void;
  };
  readonly milestone: (name: string) => string;
}

declare global {
  interface Window {
    __utraceTest?: TestSurface;
  }
}

const byId = (id: string): HTMLElement => {
  const element = document.getElementById(id);
  if (element === null) throw new Error(`test fixture is missing #${id}`);
  return element;
};

const wireProductBehaviour = (): void => {
  let rows = 0;
  byId("mutate").addEventListener("click", () => {
    rows += 1;
    const item = document.createElement("li");
    item.textContent = `Saved view ${String(rows)}`;
    item.setAttribute("aria-label", `Saved view ${String(rows)}`);
    byId("saved-view-list").append(item);
  });
  byId("same-origin-request").addEventListener("click", () => {
    void fetch("/api/echo", { method: "POST", body: JSON.stringify({ hello: "world" }) });
  });
  byId("third-party-request").addEventListener("click", () => {
    void fetch(`${String(document.body.dataset["thirdPartyOrigin"])}/echo`, { method: "GET" });
  });
  byId("xhr-request").addEventListener("click", () => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/echo");
    request.send("{}");
  });
};

const start = (): void => {
  wireProductBehaviour();
  const bootstrap = createUTraceBootstrap(window, {
    sdkInstallationId: "shopalytics-preview",
    environmentName: "preview",
    clientReleaseId: "shopalytics-main-test",
    schemaVersion: "1",
    protocolVersion: "1",
  });

  let session: UTraceSession | null = null;
  let activationError: string | null = null;
  let port: MessagePort | null = null;
  const pending = new Map<string, (reply: BridgeReply) => void>();
  let proofResolve: ((reply: BridgeReply) => void) | null = null;

  const ready = bootstrap.activate().then(
    (activated) => {
      session = activated;
    },
    (cause: unknown) => {
      activationError = cause instanceof Error ? cause.message : String(cause);
      throw cause;
    },
  );

  const connectBridge = (): Promise<BridgeReply> =>
    new Promise<BridgeReply>((resolve) => {
      proofResolve = resolve;
      const channel = new MessageChannel();
      port = channel.port1;
      channel.port1.addEventListener("message", (message: MessageEvent) => {
        const frame = message.data as BridgeFrame;
        const reply: BridgeReply = { kind: frame.kind, body: frame.payload };
        if (frame.kind === "page_proof" && proofResolve !== null) {
          proofResolve(reply);
          proofResolve = null;
          return;
        }
        const waiting = pending.get(frame.exchange_id);
        if (waiting !== undefined) {
          pending.delete(frame.exchange_id);
          waiting(reply);
        }
      });
      channel.port1.start();
      window.postMessage(
        {
          schema_version: 1,
          kind: "page_proof",
          phase: "request",
          exchange_id: crypto.randomUUID(),
          payload: { extension_protocol_version: "1.0" },
        },
        window.location.origin,
        [channel.port2],
      );
    });

  const post = (kind: string, phase: string, payload: Record<string, unknown>): string => {
    if (port === null) throw new Error("bridge is not connected");
    const exchangeId = crypto.randomUUID();
    port.postMessage({ schema_version: 1, kind, phase, exchange_id: exchangeId, payload });
    return exchangeId;
  };

  const send = (
    kind: string,
    phase: string,
    payload: Record<string, unknown>,
  ): Promise<BridgeReply> =>
    new Promise<BridgeReply>((resolve, reject) => {
      let exchangeId: string;
      try {
        exchangeId = post(kind, phase, payload);
      } catch (cause) {
        reject(cause instanceof Error ? cause : new Error(String(cause)));
        return;
      }
      pending.set(exchangeId, resolve);
    });

  /** A pause notice is one-way: the runtime answers on the control path. */
  const notify = (payload: Record<string, unknown>): void => {
    post("pause_notice", "notice", payload);
  };

  window.__utraceTest = {
    ready,
    health: (): RuntimeHealth => {
      if (session === null) throw new Error("uTrace has not activated");
      return session.health();
    },
    activationError: (): string | null => activationError,
    activateAgain: async (): Promise<string> => {
      try {
        const second = createUTraceBootstrap(window, {
          sdkInstallationId: "shopalytics-preview",
          environmentName: "preview",
          clientReleaseId: "shopalytics-main-test",
          schemaVersion: "1",
          protocolVersion: "1",
        });
        await second.activate();
        return "activated";
      } catch (cause) {
        return cause instanceof Error ? cause.message : String(cause);
      }
    },
    bridge: { connect: connectBridge, send, notify },
    milestone: (name: string): string => {
      if (session === null) throw new Error("uTrace has not activated");
      const result = session.annotations.milestone({
        milestoneId: "saved_view_created",
        severity: "info",
        priority: "normal",
        entityDefinitionId: "conversion_view",
        entityInstanceRef: "psn_1",
        attributes: [{ name: "view_name", ruleId: "safe.view_name", value: name }],
      });
      return result.accepted ? "accepted" : result.reason;
    },
  };
};

start();
