import { afterEach, describe, expect, it, vi } from "vitest";

import { ExtensionError, deliverBindingOffer, probeExtension } from "@/lib/session/extension";

const EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop";
/** As `POST /v1/sessions/{id}/extension-offer` answers it. */
const BINDING_OFFER = {
  schema_version: 1,
  offer_token: "b2ZmZXJfdG9rZW5fZXhhbXBsZV92YWx1ZQ",
  session_id: "0199a0c0-0000-7aaa-8000-0000000055e1",
  client_id: "0199a0c0-0000-7aaa-8000-00000000c101",
  extension_installation_id: "0199a0c0-0000-7aaa-8000-000000001457",
  preview_runtime_allocation_id: "0199a0c0-0000-7aaa-8000-00000000a110",
  protocol_version: "1.0",
  expires_at: "2026-10-03T09:05:00Z",
} as const;

/** The same offer as the extension receives it, field for field. */
const BINDING_OFFER_MESSAGE = {
  schema_version: 1,
  offer_token: BINDING_OFFER.offer_token,
  session_id: BINDING_OFFER.session_id,
  client_id: BINDING_OFFER.client_id,
  extension_installation_id: BINDING_OFFER.extension_installation_id,
  preview_runtime_allocation_id: BINDING_OFFER.preview_runtime_allocation_id,
  protocol_version: BINDING_OFFER.protocol_version,
  expires_at: BINDING_OFFER.expires_at,
} as const;

type Reply = Readonly<{ response?: unknown; lastError?: { message: string } }>;

function stubExtension(reply: Reply | null): ReturnType<typeof vi.fn> {
  const sendMessage = vi.fn((_id: string, _message: unknown, callback: (response: unknown) => void) => {
    if (reply === null) {
      return;
    }
    runtime.lastError = reply.lastError;
    callback(reply.response);
  });
  const runtime: { sendMessage: typeof sendMessage; lastError?: { message: string } } = { sendMessage };
  vi.stubGlobal("window", Object.assign(globalThis.window, { chrome: { runtime } }));
  return sendMessage;
}

afterEach(() => {
  Reflect.deleteProperty(window, "chrome");
  vi.useRealTimers();
});

describe("session tab to extension messaging", () => {
  it("reports installation, linkage and host permission from the extension's own answer", async () => {
    const sendMessage = stubExtension({
      response: {
        type: "utrace_preflight_result",
        account_linked: true,
        host_permission: true,
        installation_proof: "proof",
        protocol_version: "1",
      },
    });

    await expect(probeExtension(EXTENSION_ID)).resolves.toMatchObject({
      account_linked: true,
      host_permission: true,
    });
    expect(sendMessage.mock.calls[0][0]).toBe(EXTENSION_ID);
  });

  it("treats an unreachable extension as not installed", async () => {
    stubExtension({ response: undefined, lastError: { message: "Could not establish connection" } });

    await expect(probeExtension(EXTENSION_ID)).resolves.toBeNull();
  });

  it("refuses an answer that does not match the extension protocol", async () => {
    stubExtension({ response: { type: "utrace_preflight_result", account_linked: "yes" } });

    await expect(probeExtension(EXTENSION_ID)).rejects.toMatchObject({
      name: "ExtensionError",
      code: "invalid_response",
    });
  });

  it("forwards the offer to the extension unchanged and surfaces a refusal", async () => {
    const sendMessage = stubExtension({
      response: { type: "utrace_binding_offer_ack", accepted: true },
    });

    await expect(deliverBindingOffer(EXTENSION_ID, "session-1", BINDING_OFFER)).resolves.toBeUndefined();
    expect(sendMessage.mock.calls[0][1]).toEqual({
      type: "utrace_binding_offer",
      session_id: "session-1",
      binding_offer: BINDING_OFFER_MESSAGE,
    });

    stubExtension({
      response: { type: "utrace_binding_offer_ack", accepted: false, detail: "Session already bound" },
    });
    await expect(deliverBindingOffer(EXTENSION_ID, "session-1", BINDING_OFFER)).rejects.toMatchObject({
      code: "rejected",
    });
  });

  it("gives up when the extension never answers", async () => {
    vi.useFakeTimers();
    stubExtension(null);

    const attempt = probeExtension(EXTENSION_ID);
    await vi.advanceTimersByTimeAsync(5000);

    await expect(attempt).resolves.toBeNull();
  });

  it("fails clearly in a browser with no extension messaging at all", async () => {
    await expect(deliverBindingOffer(EXTENSION_ID, "session-1", BINDING_OFFER)).rejects.toBeInstanceOf(
      ExtensionError,
    );
  });
});
