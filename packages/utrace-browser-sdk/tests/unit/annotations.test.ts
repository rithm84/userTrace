/**
 * Annotation surface, manifest verification, route normalization, and the
 * extension bridge message contract.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { readElementAnnotations, resolveSurface, ANNOTATION_ATTRIBUTES } from "../../src/annotations/attributes.js";
import { decodeAnnotationManifest } from "../../src/annotations/manifest.js";
import { decodeSignedEnvelope } from "../../src/activation/signed-envelope.js";
import { validateConfig } from "../../src/core/config.js";
import { RouteTemplates } from "../../src/routes/templates.js";
import { RedactionPipeline } from "../../src/redaction/pipeline.js";
import { TaintRegistry } from "../../src/redaction/taint.js";
import { BRIDGE_FRAME_KINDS, decodeBridgeFrame } from "../../src/bridge/messages.js";
import type { JsonObject } from "../../src/core/json.js";
import { testManifestIndex, testManifestPayload } from "./helpers/manifest.js";

const index = testManifestIndex();

const element = (html: string): Element => {
  document.body.innerHTML = html;
  const first = document.body.firstElementChild;
  if (first === null) throw new Error("fixture html produced no element");
  return first;
};

beforeEach(() => {
  document.body.replaceChildren();
});

describe("declarative annotations", () => {
  it("exposes exactly the documented attribute set", () => {
    expect([...ANNOTATION_ATTRIBUTES]).toEqual([
      "data-utrace-surface",
      "data-utrace-entity",
      "data-utrace-target",
      "data-utrace-visual-target",
      "data-utrace-sensitive",
      "data-utrace-safe-value",
      "data-utrace-milestone",
      "data-utrace-severity",
      "data-utrace-priority",
    ]);
  });

  it("reads a fully annotated element", () => {
    const annotations = readElementAnnotations(
      element(
        '<button data-utrace-target="saved_view_save_button" data-utrace-entity="conversion_view:dudulemon"' +
          ' data-utrace-safe-value="safe.button_label" data-utrace-severity="info" data-utrace-priority="normal">Save</button>',
      ),
      index,
    );
    expect(annotations.targetDefinitionId).toBe("saved_view_save_button");
    expect(annotations.entity?.instance_ref_rule_id).toBe("safe.view_ref");
    expect(annotations.safeValueRuleId).toBe("safe.button_label");
    expect(annotations.severity).toBe("info");
    expect(annotations.rejections).toEqual([]);
  });

  it("fails an unknown attribute closed instead of treating it as generic", () => {
    const annotations = readElementAnnotations(element('<button data-utrace-capture="everything">Save</button>'), index);
    expect(annotations.rejections).toEqual([{ attribute: "data-utrace-capture", reason: "unknown_attribute" }]);
    expect(annotations.targetDefinitionId).toBeNull();
  });

  it("fails an unknown definition, severity, and malformed value closed", () => {
    const annotations = readElementAnnotations(
      element(
        '<button data-utrace-target="nope" data-utrace-severity="apocalyptic" data-utrace-entity="broken"' +
          ' data-utrace-sensitive="maybe">Save</button>',
      ),
      index,
    );
    expect(annotations.rejections.map((rejection) => rejection.reason).sort()).toEqual([
      "malformed_value",
      "malformed_value",
      "unknown_definition",
      "unknown_definition",
    ]);
    expect(annotations.sensitive).toBe(false);
  });

  it("refuses to use a visual-only definition as an observation target", () => {
    const annotations = readElementAnnotations(element('<section data-utrace-target="saved_view_list"></section>'), index);
    expect(annotations.rejections).toEqual([
      { attribute: "data-utrace-target", reason: "definition_visual_only" },
    ]);
  });

  it("resolves the nearest annotated surface and its sensitivity", () => {
    const host = element('<section data-utrace-surface="billing_settings"><input id="card" /></section>');
    const field = host.querySelector("#card");
    if (field === null) throw new Error("fixture is missing the field");
    expect(resolveSurface(field, index)).toEqual({ surface: "billing_settings", sensitive: true });
  });
});

describe("signed manifest", () => {
  it("indexes every declared definition", () => {
    expect(index.target("saved_view_save_button")?.visual_only).toBe(false);
    expect(index.target("saved_view_list")?.visual_only).toBe(true);
    expect(index.surface("billing_settings")?.sensitive).toBe(true);
    expect(index.milestone("saved_view_created")?.attribute_rule_ids).toContain("safe.view_name");
    expect(index.severityAllowed("info")).toBe(true);
    expect(index.severityAllowed("apocalyptic")).toBe(false);
  });

  it("allows trace injection only same-origin or to an approved server-SDK origin", () => {
    expect(index.traceInjectionAllowed("https://app.example", "https://app.example")).toBe(true);
    expect(index.traceInjectionAllowed("https://analytics.example", "https://app.example")).toBe(false);
  });

  it("rejects a manifest that names an unknown value kind", () => {
    const payload = testManifestPayload();
    const redaction = payload["redaction"] as { safe_value_rules: { allowed_kinds: string[] }[] };
    const broken = {
      ...payload,
      redaction: {
        ...redaction,
        safe_value_rules: [{ ...redaction.safe_value_rules[0], allowed_kinds: ["everything"] }],
      },
    };
    expect(() => decodeAnnotationManifest(broken)).toThrow(/unknown value kind/);
  });

  it("rejects a signed envelope that is missing a field or uses another algorithm", () => {
    expect(() =>
      decodeSignedEnvelope({ algorithm: "rsa", key_id: "k", payload_b64url: "a", signature_b64url: "b" }, "envelope"),
    ).toThrow(/one of ed25519/);
    expect(() => decodeSignedEnvelope({ algorithm: "ed25519", key_id: "k" }, "envelope")).toThrow(/non-empty string/);
  });
});

describe("static configuration", () => {
  it("accepts only the five public fields at the supported versions", () => {
    const config = validateConfig({
      sdkInstallationId: "shopalytics-preview",
      environmentName: "preview",
      clientReleaseId: "main-8f21",
      schemaVersion: "1",
      protocolVersion: "1",
    });
    expect(Object.keys(config).sort()).toEqual([
      "clientReleaseId",
      "environmentName",
      "protocolVersion",
      "schemaVersion",
      "sdkInstallationId",
    ]);
  });

  it("refuses an incompatible schema or protocol major with no substitution", () => {
    const base = {
      sdkInstallationId: "shopalytics-preview",
      environmentName: "preview",
      clientReleaseId: "main-8f21",
      schemaVersion: "1",
      protocolVersion: "1",
    };
    expect(() => validateConfig({ ...base, schemaVersion: "2" })).toThrow(/implements schema version 1/);
    expect(() => validateConfig({ ...base, protocolVersion: "0" })).toThrow(/implements protocol version 1/);
    expect(() => validateConfig({ ...base, sdkInstallationId: "a bad id!" })).toThrow(/sdkInstallationId/);
  });
});

describe("route normalization", () => {
  const routes = new RouteTemplates(["/", "/dashboard/conversions", "/dashboard/conversions/:viewId"]);
  const redaction = (): RedactionPipeline => new RedactionPipeline(index.manifest.redaction, new TaintRegistry());

  it("normalizes a known route and never exports the raw query or fragment", () => {
    const state = routes.resolve(new URL("https://app.example/dashboard/conversions?tab=x#y"), redaction());
    expect(state.route_template).toBe("/dashboard/conversions");
    expect(state.unknown_route).toBe(false);
    expect(state.has_query).toBe(true);
    expect(state.has_fragment).toBe(true);
    expect(JSON.stringify(state)).not.toContain("tab=x");
  });

  it("redacts a route parameter through the pipeline", () => {
    const state = routes.resolve(new URL("https://app.example/dashboard/conversions/abc123"), redaction());
    expect(state.route_template).toBe("/dashboard/conversions/:viewId");
    expect(state.params[0]?.name).toBe("viewId");
    expect(state.params[0]?.value.output_class).toBe("structural_operational_safe");
  });

  it("reports an unrecognized route as unknown with structural metadata only", () => {
    const state = routes.resolve(new URL("https://app.example/admin/secret/path"), redaction());
    expect(state.route_template).toBeUndefined();
    expect(state.unknown_route).toBe(true);
    expect(state.segment_count).toBe(3);
    expect(JSON.stringify(state)).not.toContain("secret");
  });
});

describe("extension bridge frames", () => {
  const frame = (kind: string, phase: string, payload: JsonObject): JsonObject => ({
    schema_version: 1,
    kind,
    phase,
    exchange_id: "0199a0c0-0000-7aaa-8000-000000000201",
    payload,
  });

  it("names exactly the five frame kinds", () => {
    expect([...BRIDGE_FRAME_KINDS]).toEqual([
      "page_proof",
      "target_query",
      "target_geometry",
      "detail_query",
      "pause_notice",
    ]);
  });

  it("decodes the page-proof handshake the extension opens the channel with", () => {
    expect(
      decodeBridgeFrame(frame("page_proof", "request", { extension_protocol_version: "1.0" })),
    ).toMatchObject({ kind: "page_proof", extension_protocol_version: "1.0" });
  });

  it("decodes a structured target query by definition and by target ids", () => {
    expect(
      decodeBridgeFrame(
        frame("target_query", "request", {
          document_attachment_id: "0199a0c0-0000-7aaa-8000-0000000a7701",
          query: {
            by: "definition",
            definition_id: "saved_view_save_button",
            effect: "visual_guidance",
            visibility: "visible",
          },
          deadline_ms: 400,
        }),
      ),
    ).toMatchObject({
      kind: "target_query",
      query: { by: "definition", definition_id: "saved_view_save_button", visibility: "visible" },
    });
    expect(
      decodeBridgeFrame(
        frame("target_query", "request", {
          document_attachment_id: "0199a0c0-0000-7aaa-8000-0000000a7701",
          query: { by: "target_ids", target_ids: ["tgt_1_a"] },
          deadline_ms: 400,
        }),
      ),
    ).toMatchObject({ kind: "target_query", query: { by: "target_ids", target_ids: ["tgt_1_a"] } });
  });

  it("decodes a pause notice and a detail query", () => {
    expect(
      decodeBridgeFrame(
        frame("pause_notice", "notice", {
          session_id: "0199a0c0-0000-7aaa-8000-0000000055e1",
          document_attachment_id: "0199a0c0-0000-7aaa-8000-0000000a7701",
          pause_state: "paused",
          session_control_revision: 4,
        }),
      ),
    ).toMatchObject({ kind: "pause_notice", pause_state: "paused" });
    expect(
      decodeBridgeFrame(
        frame("detail_query", "request", {
          document_attachment_id: "0199a0c0-0000-7aaa-8000-0000000a7701",
          expected_registry_revision: 4,
          target_id: "tgt_1_a",
          fields: ["role"],
          depth_limit: 1,
          size_limit_bytes: 1024,
          deadline_ms: 500,
        }),
      ),
    ).toMatchObject({ kind: "detail_query", target_id: "tgt_1_a", depth_limit: 1 });
  });

  it("fails closed on an unknown kind, another schema major, a wrong phase, or an inbound geometry answer", () => {
    expect(() => decodeBridgeFrame(frame("browser_command", "request", {}))).toThrow(/one of/);
    expect(() =>
      decodeBridgeFrame({ ...frame("pause_notice", "notice", {}), schema_version: 2 }),
    ).toThrow(/speaks schema 2/);
    expect(() =>
      decodeBridgeFrame(frame("page_proof", "response", { extension_protocol_version: "1.0" })),
    ).toThrow(/one of request/);
    expect(() =>
      decodeBridgeFrame(frame("target_geometry", "response", { targets: [] })),
    ).toThrow(/never inbound/);
  });
});
