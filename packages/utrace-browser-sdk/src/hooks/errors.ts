/**
 * Frontend error capture.
 *
 * Errors are typed and sanitized before export. The browser does not stream
 * console output. A record carries a stable message fingerprint, the error
 * class, the registered product area when one applies, and a bounded code
 * location. Source-map resolution happens after ingestion against the exact
 * registered release artifact, never from browser-provided source.
 */

import type { CodeLocation, FrontendErrorPayload } from "../observation/types.js";
import { resolveSurface } from "../annotations/attributes.js";
import type { RuntimeContext } from "../runtime/context.js";
import { NO_TARGET_OR_TRACE } from "../runtime/context.js";
import type { HookUninstall } from "./install.js";

/**
 * FNV-1a over a message with variable parts removed. The fingerprint is stable
 * across occurrences and is not reversible into the original message.
 */
const fingerprint = (message: string): string => {
  const normalized = message
    .replace(/\d+/gu, "0")
    .replace(/["'`][^"'`]*["'`]/gu, "s")
    .replace(/\s+/gu, " ")
    .trim()
    .slice(0, 400);
  let hash = 0x811c9dc5;
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= normalized.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `fp_${hash.toString(16).padStart(8, "0")}`;
};

const classOf = (reason: unknown): string => {
  if (reason instanceof Error) return reason.name;
  if (typeof reason === "string") return "StringRejection";
  if (reason === null || reason === undefined) return "EmptyRejection";
  return "NonErrorRejection";
};

const messageOf = (reason: unknown): string => {
  if (reason instanceof Error) return reason.message;
  if (typeof reason === "string") return reason;
  return "";
};

const codeLocation = (filename: string, line: number, column: number, documentOrigin: string): CodeLocation | null => {
  if (filename.length === 0) return null;
  let scriptRef: string;
  try {
    const url = new URL(filename, documentOrigin);
    scriptRef = url.origin === documentOrigin ? url.pathname : "cross_origin";
  } catch {
    scriptRef = "unparsable_script_url";
  }
  return { script_ref: scriptRef, line, column };
};

const currentProductArea = (context: RuntimeContext): string | undefined => {
  const active = context.view.document.activeElement;
  if (active === null) return undefined;
  return resolveSurface(active, context.manifest).surface;
};

export const installErrorHook = (context: RuntimeContext): HookUninstall => {
  const view = context.view;
  const documentOrigin = view.location.origin;

  const onError = (event: ErrorEvent): void => {
    const payload: FrontendErrorPayload = {
      error_class: event.error instanceof Error ? event.error.name : "ErrorEvent",
      message_fingerprint: fingerprint(event.message),
      product_area: currentProductArea(context),
      code_location: codeLocation(event.filename, event.lineno, event.colno, documentOrigin) ?? undefined,
      source: "error_event",
      handled: false,
    };
    context.emit({ type: "error.frontend", body: payload }, NO_TARGET_OR_TRACE);
  };

  const onRejection = (event: PromiseRejectionEvent): void => {
    const reason: unknown = event.reason;
    const payload: FrontendErrorPayload = {
      error_class: classOf(reason),
      message_fingerprint: fingerprint(messageOf(reason)),
      product_area: currentProductArea(context),
      source: "unhandled_rejection",
      handled: false,
    };
    context.emit({ type: "error.frontend", body: payload }, NO_TARGET_OR_TRACE);
  };

  view.addEventListener("error", onError);
  view.addEventListener("unhandledrejection", onRejection);
  return (): void => {
    view.removeEventListener("error", onError);
    view.removeEventListener("unhandledrejection", onRejection);
  };
};
