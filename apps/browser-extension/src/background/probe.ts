/**
 * The page-proof probe.
 *
 * After the session tab creates a pending offer, focusing a tab triggers only
 * this lightweight probe: one main-world message that asks a compatible
 * embedded SDK for its signed page proof. A document that does not answer is
 * left untouched — no interface, no telemetry, no commands, no inspection —
 * and nothing is injected into it beyond the probe itself.
 */
import { PROTOCOL_VERSION, SCHEMA_VERSION, readPageProofClaims } from "../protocol/common";
import { parsePageProofResponse, type PageProofResponse } from "../protocol/bridge";
import type { PageProofClaims } from "../protocol/common";
import { newUuidV7 } from "../shared/identifiers";

const PROBE_TIMEOUT_MS = 400;

export type ProbeOutcome = Readonly<{
  response: PageProofResponse;
  claims: PageProofClaims;
  documentOrigin: string;
}>;

/**
 * Runs in the page's main world. It is serialised by `chrome.scripting`, so it
 * closes over nothing and returns only structured-cloneable data.
 */
function pageProofProbe(
  exchangeId: string,
  schemaVersion: number,
  protocolVersion: string,
  timeoutMs: number,
): Promise<{ response: unknown; documentOrigin: string } | null> {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    let settled = false;

    const finish = (response: unknown): void => {
      if (settled) {
        return;
      }
      settled = true;
      channel.port1.close();
      resolve(response === null ? null : { response, documentOrigin: window.origin });
    };

    channel.port1.onmessage = (event: MessageEvent): void => {
      finish(event.data);
    };
    channel.port1.start();

    window.postMessage(
      {
        schema_version: schemaVersion,
        kind: "page_proof",
        phase: "request",
        exchange_id: exchangeId,
        payload: { extension_protocol_version: protocolVersion },
      },
      window.origin,
      [channel.port2],
    );

    setTimeout(() => {
      finish(null);
    }, timeoutMs);
  });
}

/** Returns the SDK's page proof, or null when the document has no uTrace SDK. */
export async function probeTabForPageProof(tabId: number): Promise<ProbeOutcome | null> {
  const exchangeId = newUuidV7();
  const injections = await chrome.scripting.executeScript({
    target: { tabId, frameIds: [0] },
    world: "MAIN",
    injectImmediately: true,
    func: pageProofProbe,
    args: [exchangeId, SCHEMA_VERSION, PROTOCOL_VERSION, PROBE_TIMEOUT_MS],
  });

  const result = injections[0]?.result;
  if (result === null || result === undefined) {
    return null;
  }

  const response = parsePageProofResponse(result.response);
  if (response.exchange_id !== exchangeId) {
    return null;
  }
  const claims = readPageProofClaims(response.payload.page_proof);
  if (claims.origin !== result.documentOrigin) {
    return null;
  }
  if (claims.document_attachment_id !== response.payload.document_attachment_id) {
    return null;
  }
  return { response, claims, documentOrigin: result.documentOrigin };
}
