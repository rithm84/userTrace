/**
 * The handshake that pairs the isolated-world content script with the
 * main-world bridge for one document.
 *
 * The service worker mints the nonce and hands it to both sides, so the content
 * script attaches to the bridge it injected rather than to any main-world
 * script that happens to be listening. The nonce authorizes nothing: authority
 * comes from the control plane's validation of the SDK's signed page proof.
 */

export const BRIDGE_HANDSHAKE_GLOBAL = "__utraceBridgeHandshake";
const ATTACH_KIND = "utrace.bridge.attach";

export type BridgeAttachMessage = Readonly<{ kind: typeof ATTACH_KIND; nonce: string }>;

export function createAttachMessage(nonce: string): BridgeAttachMessage {
  return { kind: ATTACH_KIND, nonce };
}

export function isBridgeAttachMessage(data: unknown, nonce: string): boolean {
  if (data === null || typeof data !== "object") {
    return false;
  }
  const message = data as { kind?: unknown; nonce?: unknown };
  return message.kind === ATTACH_KIND && message.nonce === nonce;
}
