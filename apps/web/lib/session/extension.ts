"use client";

import { z } from "zod";

import type { BindingOffer } from "@/lib/api/schemas";

/**
 * The session tab's half of the extension conversation.
 *
 * The uTrace extension declares `externally_connectable` for this origin, so
 * the page can ask it two things: whether it is installed, linked and
 * permitted, and to take a single-use binding offer. Everything else — the
 * page proof, the credentials, the control-plane socket — stays inside the
 * extension. The page never sees an extension credential.
 */

const EXTENSION_PROTOCOL_VERSION = "1";
const CALL_TIMEOUT_MS = 4000;

type ChromeRuntime = Readonly<{
  sendMessage?: (
    extensionId: string,
    message: unknown,
    callback: (response: unknown) => void,
  ) => void;
  lastError?: Readonly<{ message?: string }>;
}>;

declare global {
  interface Window {
    chrome?: Readonly<{ runtime?: ChromeRuntime }>;
  }
}

export type ExtensionErrorCode = "unavailable" | "no_response" | "invalid_response" | "rejected";

export class ExtensionError extends Error {
  readonly code: ExtensionErrorCode;

  constructor(code: ExtensionErrorCode, message: string) {
    super(message);
    this.name = "ExtensionError";
    this.code = code;
  }
}

const preflightResponseSchema = z.object({
  type: z.literal("utrace_preflight_result"),
  account_linked: z.boolean(),
  host_permission: z.boolean(),
  installation_proof: z.string().min(1).nullable(),
  protocol_version: z.string().min(1),
});
export type ExtensionPreflight = z.infer<typeof preflightResponseSchema>;

const bindingAckSchema = z.object({
  type: z.literal("utrace_binding_offer_ack"),
  accepted: z.boolean(),
  detail: z.string().min(1).optional(),
});

/** Asks the extension for its installation state. `null` means it did not answer. */
export async function probeExtension(extensionId: string): Promise<ExtensionPreflight | null> {
  let response: unknown;
  try {
    response = await callExtension(extensionId, {
      type: "utrace_preflight_request",
      protocol_version: EXTENSION_PROTOCOL_VERSION,
    });
  } catch (error) {
    if (error instanceof ExtensionError && (error.code === "unavailable" || error.code === "no_response")) {
      return null;
    }
    throw error;
  }

  const parsed = preflightResponseSchema.safeParse(response);
  if (!parsed.success) {
    throw new ExtensionError(
      "invalid_response",
      `The uTrace extension answered the setup check with an unexpected message: ${JSON.stringify(parsed.error.issues).slice(0, 300)}`,
    );
  }
  return parsed.data;
}

/**
 * Hands the extension the single-use binding offer for this session, field for
 * field as `binding-offer.v1` names them.
 */
export async function deliverBindingOffer(
  extensionId: string,
  sessionId: string,
  bindingOffer: BindingOffer,
): Promise<void> {
  const response = await callExtension(extensionId, {
    type: "utrace_binding_offer",
    session_id: sessionId,
    binding_offer: {
      schema_version: bindingOffer.schema_version,
      offer_token: bindingOffer.offer_token,
      session_id: bindingOffer.session_id,
      client_id: bindingOffer.client_id,
      extension_installation_id: bindingOffer.extension_installation_id,
      preview_runtime_allocation_id: bindingOffer.preview_runtime_allocation_id,
      protocol_version: bindingOffer.protocol_version,
      expires_at: bindingOffer.expires_at,
    },
  });

  const parsed = bindingAckSchema.safeParse(response);
  if (!parsed.success) {
    throw new ExtensionError(
      "invalid_response",
      "The uTrace extension did not acknowledge the binding offer in the expected form.",
    );
  }
  if (!parsed.data.accepted) {
    throw new ExtensionError(
      "rejected",
      parsed.data.detail ?? "The uTrace extension refused the binding offer.",
    );
  }
}

function callExtension(extensionId: string, message: unknown): Promise<unknown> {
  const runtime = window.chrome?.runtime;
  const sendMessage = runtime?.sendMessage;
  if (runtime === undefined || sendMessage === undefined) {
    return Promise.reject(
      new ExtensionError("unavailable", "This browser exposes no extension messaging for uTrace."),
    );
  }

  return new Promise<unknown>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new ExtensionError("no_response", "The uTrace extension did not answer in time."));
    }, CALL_TIMEOUT_MS);

    sendMessage(extensionId, message, (response: unknown) => {
      window.clearTimeout(timer);
      const failure = runtime.lastError?.message;
      if (failure !== undefined) {
        reject(new ExtensionError("no_response", `The uTrace extension is not reachable: ${failure}`));
        return;
      }
      if (response === undefined || response === null) {
        reject(new ExtensionError("no_response", "The uTrace extension answered with nothing."));
        return;
      }
      resolve(response);
    });
  });
}
