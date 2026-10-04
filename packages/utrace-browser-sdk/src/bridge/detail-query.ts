/**
 * Authorized detail queries.
 *
 * A detail query names the client, session, document attachment, expected
 * registry revision, exact target or registered region, requested field set,
 * depth bound, size bound, and deadline. It cannot contain CSS, XPath,
 * JavaScript, unrestricted text search, or a full-page selector. Queries are
 * read-only: they cannot click, focus, type, scroll, submit, or navigate.
 *
 * A stale or ambiguous target, sensitive field, unsupported field, oversize
 * result, inactive attachment, or expired deadline fails explicitly. The
 * runtime never broadens a query to find something similar.
 */

import type { JsonObject, JsonValue } from "../core/json.js";
import { MAX_DETAIL_QUERY_BYTES, MAX_DETAIL_QUERY_DEPTH, MAX_DETAIL_QUERY_FIELDS } from "../core/limits.js";
import { computeAccessibleName, computeRole } from "../registry/accessible-name.js";
import { readElementAnnotations, resolveSurface } from "../annotations/attributes.js";
import { toSafeValue } from "../redaction/pipeline.js";
import type { SharedObservationRuntime } from "../runtime/runtime.js";

/** The authorized query, already decoded from its bridge frame. */
export interface DetailQuery {
  readonly document_attachment_id: string;
  readonly expected_registry_revision: number;
  readonly target_id: string;
  readonly fields: readonly string[];
  readonly depth_bound: number;
  readonly size_bound: number;
  readonly deadline_ms: number;
}

export type DetailQueryFailure =
  | "not_found"
  | "ambiguous"
  | "stale_target"
  | "sensitive_field"
  | "unsupported_field"
  | "oversize_result"
  | "inactive_attachment"
  | "deadline_expired"
  | "registry_revision_mismatch";

export type DetailQueryResult =
  | { readonly status: "ok"; readonly fields: JsonObject; readonly registry_revision: number }
  | { readonly status: DetailQueryFailure; readonly detail: string };

/** Structural fields every registered target supports. */
const STRUCTURAL_FIELDS: readonly string[] = [
  "role",
  "label",
  "semantic_state",
  "geometry",
  "entity_ref",
  "definition_id",
  "child_roles",
  "surface",
];

/** Fields that would expose an unapproved value are rejected before evaluation. */
const SENSITIVE_FIELDS: readonly string[] = ["value", "text", "inner_html", "attributes", "dataset"];

const childRoles = (element: Element, depthBound: number): JsonValue => {
  const roles: string[] = [];
  const walk = (node: Element, depth: number): void => {
    if (depth > depthBound) return;
    for (const child of node.children) {
      roles.push(computeRole(child));
      walk(child, depth + 1);
    }
  };
  walk(element, 1);
  return roles.slice(0, 64);
};

export const evaluateDetailQuery = (
  runtime: SharedObservationRuntime,
  request: DetailQuery,
  nowMs: number,
  receivedAtMs: number,
): DetailQueryResult => {
  if (runtime.state !== "ready") {
    return { status: "inactive_attachment", detail: `runtime state is ${runtime.state}` };
  }
  if (request.document_attachment_id !== runtime.attestationDocumentAttachmentId) {
    return { status: "inactive_attachment", detail: "document attachment does not match this runtime" };
  }
  if (nowMs - receivedAtMs > request.deadline_ms) {
    return { status: "deadline_expired", detail: `deadline of ${String(request.deadline_ms)}ms elapsed` };
  }
  if (request.expected_registry_revision !== runtime.registry.registryRevision) {
    return {
      status: "registry_revision_mismatch",
      detail: `expected ${String(request.expected_registry_revision)}, current ${String(runtime.registry.registryRevision)}`,
    };
  }
  if (request.fields.length === 0 || request.fields.length > MAX_DETAIL_QUERY_FIELDS) {
    return { status: "unsupported_field", detail: `field count ${String(request.fields.length)} is out of bounds` };
  }
  if (request.depth_bound < 0 || request.depth_bound > MAX_DETAIL_QUERY_DEPTH) {
    return { status: "unsupported_field", detail: `depth bound ${String(request.depth_bound)} is out of bounds` };
  }
  const resolved = runtime.registry.byId(request.target_id);
  if (resolved.status === "stale_target") {
    return { status: "stale_target", detail: `target ${request.target_id} is unmounted` };
  }
  if (resolved.status === "not_found") {
    return { status: "not_found", detail: `target ${request.target_id} is not registered` };
  }
  if (resolved.status === "ambiguous") {
    return { status: "ambiguous", detail: `target ${request.target_id} matched ${String(resolved.match_count)} instances` };
  }
  const target = resolved.target;
  const element = runtime.registry.elementFor(target.target_id);
  if (element === null) {
    return { status: "stale_target", detail: `target ${target.target_id} lost its element binding` };
  }

  const definition =
    target.definition_id === undefined ? null : runtime.manifest.target(target.definition_id);
  const allowed = new Set<string>([...STRUCTURAL_FIELDS, ...(definition?.detail_fields ?? [])]);
  const fields: Record<string, JsonValue> = {};
  const surface = resolveSurface(element, runtime.manifest);

  for (const field of request.fields) {
    if (SENSITIVE_FIELDS.includes(field)) {
      return { status: "sensitive_field", detail: `field ${field} would expose an unapproved value` };
    }
    if (!allowed.has(field)) {
      return { status: "unsupported_field", detail: `field ${field} is not declared for this target` };
    }
    switch (field) {
      case "role":
        fields["role"] = target.role;
        break;
      case "label":
        if (target.label !== undefined) fields["label"] = { ...target.label };
        break;
      case "semantic_state":
        fields["semantic_state"] = { ...target.semantic_state };
        break;
      case "geometry":
        fields["geometry"] = { ...target.geometry };
        break;
      case "entity_ref":
        if (target.entity_ref !== undefined) fields["entity_ref"] = { ...target.entity_ref };
        break;
      case "definition_id":
        if (target.definition_id !== undefined) fields["definition_id"] = target.definition_id;
        break;
      case "surface":
        if (surface.surface !== undefined) fields["surface"] = surface.surface;
        break;
      case "child_roles":
        fields["child_roles"] = childRoles(element, request.depth_bound);
        break;
      default: {
        // A manifest-declared field resolves to the element's accessible name
        // under the target definition's own safe-value rule, and nothing else.
        const source = computeAccessibleName(element);
        fields[field] =
          source === null
            ? null
            : {
                ...toSafeValue(
                  runtime.redaction.apply({
                    kind: "accessible_label",
                    value: source,
                    element,
                    inAttestedDocument: true,
                    rawBody: false,
                    approvedRuleId: readElementAnnotations(element, runtime.manifest).safeValueRuleId,
                    annotatedSensitive: surface.sensitive,
                  }),
                ),
              };
      }
    }
  }

  const bytes = new TextEncoder().encode(JSON.stringify(fields)).byteLength;
  const bound = Math.min(request.size_bound, MAX_DETAIL_QUERY_BYTES);
  if (bytes > bound) {
    return { status: "oversize_result", detail: `result is ${String(bytes)} bytes, bound is ${String(bound)}` };
  }
  return { status: "ok", fields, registry_revision: runtime.registry.registryRevision };
};
