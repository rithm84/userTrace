/**
 * Main-world bridge frames.
 *
 * One wire format is shared with the browser extension, owned by
 * `packages/utrace-contracts/schemas/extension-protocol/bridge-frame.v1.schema.json`
 * and its five per-kind schemas. Every frame is a structured-cloneable object
 * `{schema_version, kind, phase, exchange_id, payload}`; a request and its
 * answer share the same `exchange_id`.
 *
 * | Kind | Request | Answer |
 * | --- | --- | --- |
 * | `page_proof` | extension | SDK, and again unsolicited when the attachment changes |
 * | `target_query` | extension | SDK, as `target_geometry` |
 * | `detail_query` | extension | SDK |
 * | `pause_notice` | extension, one-way | none |
 *
 * The extension hands over the port: it creates the `MessageChannel` and posts
 * the `page_proof` request on `window` with `port2` transferred. That one
 * handshake serves both the service worker's lightweight page-proof probe and
 * the content script's long-lived bridge, so the SDK implements exactly one.
 *
 * An unknown kind, phase, or field fails closed. Nothing here is coerced.
 */

import type { JsonObject, JsonValue } from "../core/json.js";
import { asObject, requireEnum, requireInteger, requireObject, requireString, requireStringArray } from "../core/json.js";
import { BRIDGE_SCHEMA_VERSION } from "../core/versions.js";

/** Kinds the SDK accepts on the port or on the discovery message. */
export const BRIDGE_FRAME_KINDS = ["page_proof", "target_query", "target_geometry", "detail_query", "pause_notice"] as const;
export type BridgeFrameKind = (typeof BRIDGE_FRAME_KINDS)[number];

/** Structured target query. Never CSS, XPath, JavaScript, coordinates, or free text. */
export type BridgeTargetQuery =
  | { readonly by: "target_ids"; readonly target_ids: readonly string[] }
  | {
      readonly by: "definition";
      readonly definition_id: string;
      readonly entity_ref: string | null;
      readonly effect: "observe" | "visual_guidance" | "any";
      readonly route_template: string | null;
      readonly visibility: "visible" | "offscreen" | "any";
    };

export interface PageProofRequest {
  readonly kind: "page_proof";
  readonly exchange_id: string;
  readonly extension_protocol_version: string;
}

export interface TargetQueryRequest {
  readonly kind: "target_query";
  readonly exchange_id: string;
  readonly document_attachment_id: string;
  readonly query: BridgeTargetQuery;
  readonly deadline_ms: number;
}

export interface DetailQueryRequest {
  readonly kind: "detail_query";
  readonly exchange_id: string;
  readonly document_attachment_id: string;
  readonly expected_registry_revision: number;
  readonly target_id: string;
  readonly fields: readonly string[];
  readonly depth_limit: number;
  readonly size_limit_bytes: number;
  readonly deadline_ms: number;
}

export interface PauseNotice {
  readonly kind: "pause_notice";
  readonly exchange_id: string;
  readonly session_id: string;
  readonly document_attachment_id: string;
  readonly pause_state: "paused" | "running" | "ended";
  readonly session_control_revision: number;
}

export type BridgeRequest = PageProofRequest | TargetQueryRequest | DetailQueryRequest | PauseNotice;

const requireSchemaVersion = (frame: JsonObject): void => {
  const version = frame["schema_version"];
  if (version !== BRIDGE_SCHEMA_VERSION) {
    throw new Error(
      `uTrace bridge frame speaks schema ${JSON.stringify(version)}, this runtime speaks ${String(BRIDGE_SCHEMA_VERSION)}`,
    );
  }
};

const decodeTargetQuery = (query: JsonObject, path: string): BridgeTargetQuery => {
  const by = requireEnum(query, "by", ["target_ids", "definition"] as const, path);
  if (by === "target_ids") {
    return { by, target_ids: requireStringArray(query, "target_ids", path) };
  }
  const optional = (field: string): string | null => {
    const value = query[field];
    if (value === undefined || value === null) return null;
    return requireString(query, field, path);
  };
  return {
    by,
    definition_id: requireString(query, "definition_id", path),
    entity_ref: optional("entity_ref"),
    effect: requireEnum(query, "effect", ["observe", "visual_guidance", "any"] as const, path),
    route_template: optional("route_template"),
    visibility: requireEnum(query, "visibility", ["visible", "offscreen", "any"] as const, path),
  };
};

/**
 * Decodes one inbound frame. `data` is whatever the port or the discovery
 * message delivered, so it is untrusted until this function returns.
 */
export const decodeBridgeFrame = (data: JsonValue): BridgeRequest => {
  const path = "bridge_frame";
  const frame = asObject(data, path);
  requireSchemaVersion(frame);
  const kind = requireEnum(frame, "kind", BRIDGE_FRAME_KINDS, path);
  const exchangeId = requireString(frame, "exchange_id", path);
  const payload = requireObject(frame, "payload", path);
  const payloadPath = `${path}.payload`;
  switch (kind) {
    case "page_proof": {
      requireEnum(frame, "phase", ["request"] as const, path);
      return {
        kind,
        exchange_id: exchangeId,
        extension_protocol_version: requireString(payload, "extension_protocol_version", payloadPath),
      };
    }
    case "target_query":
      requireEnum(frame, "phase", ["request"] as const, path);
      return {
        kind,
        exchange_id: exchangeId,
        document_attachment_id: requireString(payload, "document_attachment_id", payloadPath),
        query: decodeTargetQuery(requireObject(payload, "query", payloadPath), `${payloadPath}.query`),
        deadline_ms: requireInteger(payload, "deadline_ms", payloadPath),
      };
    case "detail_query":
      requireEnum(frame, "phase", ["request"] as const, path);
      return {
        kind,
        exchange_id: exchangeId,
        document_attachment_id: requireString(payload, "document_attachment_id", payloadPath),
        expected_registry_revision: requireInteger(payload, "expected_registry_revision", payloadPath),
        target_id: requireString(payload, "target_id", payloadPath),
        fields: requireStringArray(payload, "fields", payloadPath),
        depth_limit: requireInteger(payload, "depth_limit", payloadPath),
        size_limit_bytes: requireInteger(payload, "size_limit_bytes", payloadPath),
        deadline_ms: requireInteger(payload, "deadline_ms", payloadPath),
      };
    case "pause_notice":
      requireEnum(frame, "phase", ["notice"] as const, path);
      return {
        kind,
        exchange_id: exchangeId,
        session_id: requireString(payload, "session_id", payloadPath),
        document_attachment_id: requireString(payload, "document_attachment_id", payloadPath),
        pause_state: requireEnum(payload, "pause_state", ["paused", "running", "ended"] as const, payloadPath),
        session_control_revision: requireInteger(payload, "session_control_revision", payloadPath),
      };
    case "target_geometry":
      throw new Error("uTrace target_geometry travels from the SDK to the extension, never inbound");
  }
};

/** Builds one outbound frame with the shared envelope fields. */
export const bridgeFrame = (
  kind: BridgeFrameKind,
  exchangeId: string,
  payload: JsonObject,
): JsonObject => ({
  schema_version: BRIDGE_SCHEMA_VERSION,
  kind,
  phase: "response",
  exchange_id: exchangeId,
  payload,
});
