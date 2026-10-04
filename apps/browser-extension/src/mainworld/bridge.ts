/**
 * The main-world bridge.
 *
 * It connects the content script to the embedded SDK and the shared browser
 * observation runtime over a fresh per-document `MessageChannel`, relaying the
 * five JSON message kinds the SDK implements: `page_proof`, `target_query`,
 * `target_geometry`, `detail_query`, and `pause_notice`.
 *
 * It owns no user interface, session credential, authorization decision, or
 * durable state, and it cannot reach the extension's interface tree: the
 * closed shadow root and the control-plane credential stay in the isolated
 * world and the service worker.
 */
import { parseBridgeFrame } from "../protocol/bridge";
import { PROTOCOL_VERSION, SCHEMA_VERSION } from "../protocol/common";
import { BRIDGE_HANDSHAKE_GLOBAL, isBridgeAttachMessage } from "../shared/bridgeHandshake";

type Ports = { content: MessagePort | null; sdk: MessagePort | null };

/** The service worker sets the nonce immediately before injecting this file. */
function takeHandshakeNonce(): string {
  const carrier = window as unknown as Record<string, unknown>;
  const nonce = carrier[BRIDGE_HANDSHAKE_GLOBAL];
  Reflect.deleteProperty(carrier, BRIDGE_HANDSHAKE_GLOBAL);
  if (typeof nonce !== "string" || nonce.length === 0) {
    throw new Error("The uTrace page bridge was injected without its handshake nonce.");
  }
  return nonce;
}

function install(nonce: string): void {
  const ports: Ports = { content: null, sdk: null };

  const relayToContent = (event: MessageEvent): void => {
    const port = ports.content;
    if (port === null) {
      return;
    }
    port.postMessage(parseBridgeFrame(event.data));
  };

  const relayToSdk = (event: MessageEvent): void => {
    const port = ports.sdk;
    if (port === null) {
      return;
    }
    port.postMessage(parseBridgeFrame(event.data));
  };

  const openSdkChannel = (): void => {
    const channel = new MessageChannel();
    ports.sdk = channel.port1;
    channel.port1.onmessage = relayToContent;
    channel.port1.start();
    window.postMessage(
      {
        schema_version: SCHEMA_VERSION,
        kind: "page_proof",
        phase: "request",
        exchange_id: nonce,
        payload: { extension_protocol_version: PROTOCOL_VERSION },
      },
      window.origin,
      [channel.port2],
    );
  };

  window.addEventListener("message", (event: MessageEvent): void => {
    if (event.source !== window || !isBridgeAttachMessage(event.data, nonce)) {
      return;
    }
    const port = event.ports[0];
    if (port === undefined || ports.content !== null) {
      return;
    }
    ports.content = port;
    port.onmessage = relayToSdk;
    port.start();
    openSdkChannel();
  });
}

install(takeHandshakeNonce());
