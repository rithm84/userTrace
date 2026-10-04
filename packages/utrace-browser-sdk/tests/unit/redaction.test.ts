/**
 * Redaction pipeline behaviour.
 *
 * The seven steps run in a fixed order, later steps may only restrict further,
 * platform exclusions cannot be re-enabled by any client rule, taint survives
 * reparenting, and the audit never carries a rejected value.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { REDACTION_STEPS, restrictionRank, lengthBucket, mostRestrictive } from "../../src/redaction/classes.js";
import { RedactionPipeline } from "../../src/redaction/pipeline.js";
import { TaintRegistry } from "../../src/redaction/taint.js";
import { detectSensitiveContent } from "../../src/redaction/detectors.js";
import { testManifestIndex } from "./helpers/manifest.js";

const policy = (): ReturnType<typeof testManifestIndex>["manifest"]["redaction"] =>
  testManifestIndex().manifest.redaction;

const build = (): { pipeline: RedactionPipeline; taint: TaintRegistry } => {
  const taint = new TaintRegistry();
  return { pipeline: new RedactionPipeline(policy(), taint), taint };
};

const element = (html: string): Element => {
  const host = document.createElement("div");
  host.innerHTML = html;
  const first = host.firstElementChild;
  if (first === null) throw new Error("fixture html produced no element");
  document.body.append(host);
  return first;
};

beforeEach(() => {
  document.body.replaceChildren();
});

describe("output classes", () => {
  it("orders the five classes so a later step can only restrict further", () => {
    expect(restrictionRank("never_collect")).toBeGreaterThan(restrictionRank("metadata_only"));
    expect(restrictionRank("metadata_only")).toBeGreaterThan(restrictionRank("pseudonymized"));
    expect(restrictionRank("pseudonymized")).toBeGreaterThan(restrictionRank("bounded_safe_value"));
    expect(restrictionRank("bounded_safe_value")).toBeGreaterThan(restrictionRank("structural_operational_safe"));
    expect(mostRestrictive("bounded_safe_value", "metadata_only")).toBe("metadata_only");
  });

  it("evaluates exactly the seven documented steps in order", () => {
    expect(REDACTION_STEPS).toEqual([
      "platform_exclusions",
      "session_document_scope",
      "signed_client_policy",
      "structural_inheritance",
      "per_value_evaluation",
      "defensive_detection",
      "normalization_and_limits",
    ]);
  });

  it("buckets lengths without exporting the length itself", () => {
    expect(lengthBucket(0)).toBe("empty");
    expect(lengthBucket(3)).toBe("len_1_3");
    expect(lengthBucket(22)).toBe("len_11_30");
    expect(lengthBucket(9000)).toBe("len_over_500");
  });
});

describe("step one: platform exclusions", () => {
  it("never collects a password field, whatever the client rule says", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "hunter2",
      element: element('<input type="password" name="password" />'),
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("never_collect");
    expect(outcome.decided_at_step).toBe("platform_exclusions");
    expect(outcome.value).toBeNull();
  });

  it("never collects a payment field identified by autocomplete", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "4242424242424242",
      element: element('<input autocomplete="cc-number" />'),
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("never_collect");
    expect(outcome.rule_id).toContain("autocomplete.cc-number");
  });

  it("never collects an unrestricted body", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "user_text",
      value: '{"secret":true}',
      element: null,
      inAttestedDocument: true,
      rawBody: true,
      approvedRuleId: null,
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("never_collect");
    expect(outcome.rule_id).toBe("platform.raw_body");
  });
});

describe("step two: authenticated document scope", () => {
  it("never collects a value from outside the attested document", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "accessible_label",
      value: "Cross-origin label",
      element: null,
      inAttestedDocument: false,
      rawBody: false,
      approvedRuleId: "safe.button_label",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("never_collect");
    expect(outcome.decided_at_step).toBe("session_document_scope");
  });
});

describe("steps three and five: signed rules", () => {
  it("passes a bounded safe value when the exact signed rule accepts it", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "TikTok sizing drop-off",
      element: element('<input name="view_name" />'),
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("bounded_safe_value");
    expect(outcome.value).toBe("TikTok sizing drop-off");
  });

  it("falls back to metadata only when the rule does not allow that value kind", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "accessible_label",
      value: "TikTok sizing drop-off",
      element: element('<input name="view_name" />'),
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("metadata_only");
    expect(outcome.rule_id).toBe("safe.view_name.kind_not_allowed");
    expect(outcome.value).toBeNull();
  });

  it("fails closed on an unknown rule id", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "anything",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.not_in_the_manifest",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("metadata_only");
    expect(outcome.rule_id).toContain("unknown_rule");
  });

  it("rejects a value the signed pattern refuses", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "<script>alert(1)</script>",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("metadata_only");
    expect(outcome.rule_id).toBe("safe.view_name.pattern_mismatch");
  });

  it("defaults user-authored text to metadata only with no rule", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "user_text",
      value: "a note the user typed",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: null,
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("metadata_only");
    expect(outcome.value).toBeNull();
    expect(outcome.length_bucket).toBe("len_11_30");
  });

  it("pseudonymizes stably inside one document and never exports the original", () => {
    const { pipeline } = build();
    const first = pipeline.apply({
      kind: "annotation_attribute",
      value: "dudulemon-tiktok",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_ref",
      annotatedSensitive: false,
    });
    const second = pipeline.apply({
      kind: "annotation_attribute",
      value: "dudulemon-tiktok",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_ref",
      annotatedSensitive: false,
    });
    expect(first.output_class).toBe("pseudonymized");
    expect(first.value).toMatch(/^psn_\d+$/);
    expect(second.value).toBe(first.value);
    expect(first.value).not.toContain("dudulemon");
  });
});

describe("step four: structural inheritance and taint", () => {
  it("restricts a value inside an annotated sensitive surface", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "TikTok sizing drop-off",
      element: element('<input name="view_name" />'),
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: true,
    });
    expect(outcome.output_class).toBe("metadata_only");
    expect(outcome.rule_id).toBe("structural.annotated_sensitive");
  });

  it("keeps taint after the node is reparented into an allowed region", () => {
    const { pipeline, taint } = build();
    const sensitive = element('<section data-utrace-sensitive="true"><input name="view_name" /></section>');
    const field = sensitive.querySelector("input");
    if (field === null) throw new Error("fixture is missing the field");
    taint.taintSubtree(sensitive);

    const allowed = element("<section></section>");
    allowed.append(field);
    expect(taint.isTainted(field)).toBe(true);

    const outcome = pipeline.apply({
      kind: "form_value",
      value: "TikTok sizing drop-off",
      element: field,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("metadata_only");
    expect(outcome.rule_id).toBe("structural.tainted_ancestor");
  });

  it("taints a node added beneath a tainted parent", () => {
    const { taint } = build();
    const sensitive = element('<section data-utrace-sensitive="true"></section>');
    taint.taintSubtree(sensitive);
    const added = document.createElement("span");
    sensitive.append(added);
    taint.onNodeAdded(added);
    expect(taint.isTainted(added)).toBe(true);
  });
});

describe("step six: defensive detection", () => {
  it("only ever restricts further", () => {
    expect(detectSensitiveContent("plain text").restrictTo).toBeNull();
    expect(detectSensitiveContent("maya@dudulemon.example").restrictTo).toBe("pseudonymized");
    expect(detectSensitiveContent("4242 4242 4242 4242").restrictTo).toBe("never_collect");
    expect(detectSensitiveContent("-----BEGIN PRIVATE KEY-----").restrictTo).toBe("never_collect");
  });

  it("restricts an operational value that turns out to contain a token", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "operational",
      value: "authorization: Bearer abcdefghijklmnop",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: null,
      annotatedSensitive: false,
    });
    expect(outcome.output_class).toBe("never_collect");
    expect(outcome.decided_at_step).toBe("defensive_detection");
  });
});

describe("step seven and the audit", () => {
  it("normalizes and bounds an approved value", () => {
    const { pipeline } = build();
    const outcome = pipeline.apply({
      kind: "form_value",
      value: "  TikTok   sizing    drop-off  ",
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: "safe.view_name",
      annotatedSensitive: false,
    });
    expect(outcome.value).toBe("TikTok sizing drop-off");
  });

  it("records rule ids, versions, classes, and counts but never a value", () => {
    const { pipeline } = build();
    for (let index = 0; index < 3; index += 1) {
      pipeline.apply({
        kind: "form_value",
        value: "hunter2",
        element: element('<input type="password" />'),
        inAttestedDocument: true,
        rawBody: false,
        approvedRuleId: null,
        annotatedSensitive: false,
      });
    }
    const entries = pipeline.audit.entries();
    const excluded = entries.find((entry) => entry.output_class === "never_collect");
    expect(excluded?.count).toBe(3);
    expect(excluded?.redaction_version).toBe("r-1");
    expect(excluded?.policy_version).toBe("p-1");
    expect(JSON.stringify(entries)).not.toContain("hunter2");
    expect(pipeline.audit.totalFor("never_collect")).toBe(3);
  });
});
