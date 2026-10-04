/**
 * The redaction pipeline.
 *
 * Redaction runs before a value enters an observation buffer, in the fixed
 * seven-step order:
 *
 *   1. non-overridable platform exclusions
 *   2. authenticated session and document scope
 *   3. signed client annotation and redaction policy
 *   4. structural inheritance from sensitive surfaces and ancestors
 *   5. per-value evaluation at observation time
 *   6. defensive sensitive-data detection, which may only restrict further
 *   7. normalization, length and cardinality limits, and schema validation
 *
 * Each step may raise the restriction rank; none may lower it. The pipeline
 * returns the surviving representation and records an audit entry that never
 * contains the rejected value.
 */

import { MAX_SAFE_VALUE_LENGTH } from "../core/limits.js";
import type { LengthBucket, OutputClass, RedactionStep, ValueKind } from "./classes.js";
import { lengthBucket, mostRestrictive, restrictionRank } from "./classes.js";
import { RedactionAudit } from "./audit.js";
import { detectSensitiveContent } from "./detectors.js";
import type { PlatformExclusionContext } from "./platform-exclusions.js";
import { evaluatePlatformExclusions } from "./platform-exclusions.js";
import type { SafeValue } from "../observation/types.js";
import type { RedactionPolicy, SafeValueRule } from "./policy.js";
import { checkSafeValueRule, findSafeValueRule } from "./policy.js";
import type { TaintRegistry } from "./taint.js";

/** Maximum distinct values one safe-value rule may export inside one document. */
const MAX_RULE_CARDINALITY = 200;

const CONTROL_CHARACTERS = /[\p{Cc}\p{Cf}]/gu;

export interface RedactionRequest {
  readonly kind: ValueKind;
  readonly value: string;
  /** The element the value came from, when there is one. */
  readonly element: Element | null;
  /** False when the value did not originate inside the attested document. */
  readonly inAttestedDocument: boolean;
  /** True for unrestricted request or response bodies. */
  readonly rawBody: boolean;
  /** Signed safe-value rule the annotation surface named, when any. */
  readonly approvedRuleId: string | null;
  /** True when an annotation marked the field or its surface sensitive. */
  readonly annotatedSensitive: boolean;
}

export interface RedactionOutcome {
  readonly output_class: OutputClass;
  readonly value: string | null;
  readonly length_bucket: LengthBucket;
  readonly rule_id: string;
  readonly decided_at_step: RedactionStep;
}

interface StepState {
  outputClass: OutputClass;
  ruleId: string;
  step: RedactionStep;
  rule: SafeValueRule | null;
}

const exclusionContext = (request: RedactionRequest): PlatformExclusionContext => {
  const element = request.element;
  const tagName = element === null ? null : element.tagName.toLowerCase();
  const inputType =
    element !== null && tagName === "input" ? (element.getAttribute("type") ?? "text").toLowerCase() : null;
  const autocomplete =
    element === null
      ? []
      : (element.getAttribute("autocomplete") ?? "")
          .toLowerCase()
          .split(/\s+/)
          .filter((token) => token.length > 0);
  const identifiers =
    element === null
      ? []
      : [element.getAttribute("name") ?? "", element.getAttribute("id") ?? ""]
          .map((value) => value.toLowerCase())
          .filter((value) => value.length > 0);
  return {
    kind: request.kind,
    tagName,
    inputType,
    autocomplete,
    identifiers,
    foreignOrigin: false,
    rawBody: request.rawBody,
  };
};

/** Collapses whitespace, normalizes, strips control characters, and truncates. */
const normalize = (value: string, maxLength: number): string => {
  const stripped = value.normalize("NFC").replace(CONTROL_CHARACTERS, " ");
  const collapsed = stripped.replace(/\s+/gu, " ").trim();
  return collapsed.length <= maxLength ? collapsed : collapsed.slice(0, maxLength);
};

export class RedactionPipeline {
  readonly #policy: RedactionPolicy;
  readonly #taint: TaintRegistry;
  readonly #audit: RedactionAudit;
  readonly #pseudonyms = new Map<string, string>();
  readonly #cardinality = new Map<string, Set<string>>();
  #pseudonymOrdinal = 0;

  constructor(policy: RedactionPolicy, taint: TaintRegistry) {
    this.#policy = policy;
    this.#taint = taint;
    this.#audit = new RedactionAudit(policy.redaction_version, policy.policy_version);
  }

  get audit(): RedactionAudit {
    return this.#audit;
  }

  get policy(): RedactionPolicy {
    return this.#policy;
  }

  /** Runs all seven steps and returns the surviving representation. */
  apply(request: RedactionRequest): RedactionOutcome {
    const state: StepState = {
      outputClass: "structural_operational_safe",
      ruleId: "policy.default",
      step: "platform_exclusions",
      rule: null,
    };

    const platform = evaluatePlatformExclusions(exclusionContext(request));
    if (platform.excluded) {
      return this.#finish("never_collect", null, request.value.length, platform.rule_id, "platform_exclusions");
    }

    if (!request.inAttestedDocument) {
      return this.#finish(
        "never_collect",
        null,
        request.value.length,
        "scope.foreign_document",
        "session_document_scope",
      );
    }

    this.#applySignedPolicy(request, state);
    this.#applyStructuralInheritance(request, state);
    const perValue = this.#applyPerValueEvaluation(request, state);
    if (perValue !== null) return perValue;

    if (restrictionRank(state.outputClass) >= restrictionRank("metadata_only")) {
      return this.#finish(state.outputClass, null, request.value.length, state.ruleId, state.step);
    }

    const detection = detectSensitiveContent(request.value);
    if (detection.restrictTo !== null) {
      const restricted = mostRestrictive(state.outputClass, detection.restrictTo);
      if (restricted !== state.outputClass) {
        state.outputClass = restricted;
        state.ruleId = detection.rule_id;
        state.step = "defensive_detection";
      }
    }
    if (restrictionRank(state.outputClass) >= restrictionRank("metadata_only")) {
      return this.#finish(state.outputClass, null, request.value.length, state.ruleId, state.step);
    }

    return this.#applyNormalization(request, state);
  }

  /** Step three: select the signed rule, or fall back to the policy default. */
  #applySignedPolicy(request: RedactionRequest, state: StepState): void {
    if (request.kind === "operational" || request.kind === "route_param" || request.kind === "annotation_attribute") {
      state.outputClass = "structural_operational_safe";
      state.ruleId = "policy.operational";
      state.step = "signed_client_policy";
    }
    if (request.kind === "user_text" || request.kind === "form_value" || request.kind === "accessible_label") {
      state.outputClass = this.#policy.default_user_value_class;
      state.ruleId = "policy.default_user_value";
      state.step = "signed_client_policy";
    }
    if (request.kind === "field_name") {
      state.outputClass = "structural_operational_safe";
      state.ruleId = "policy.field_name";
      state.step = "signed_client_policy";
    }
    if (request.approvedRuleId === null) return;
    const rule = findSafeValueRule(this.#policy, request.approvedRuleId);
    if (rule === null) {
      // An unknown safe-value rule fails closed rather than becoming a generic value.
      state.outputClass = mostRestrictive(state.outputClass, "metadata_only");
      state.ruleId = `policy.unknown_rule.${request.approvedRuleId}`;
      state.step = "signed_client_policy";
      return;
    }
    state.rule = rule;
    state.ruleId = rule.rule_id;
    state.step = "signed_client_policy";
    state.outputClass = rule.output_class;
  }

  /** Step four: sensitive surfaces and tainted ancestors restrict unconditionally. */
  #applyStructuralInheritance(request: RedactionRequest, state: StepState): void {
    const tainted = request.element !== null && this.#taint.isTainted(request.element);
    if (!tainted && !request.annotatedSensitive) return;
    state.outputClass = mostRestrictive(state.outputClass, "metadata_only");
    state.ruleId = tainted ? "structural.tainted_ancestor" : "structural.annotated_sensitive";
    state.step = "structural_inheritance";
    state.rule = null;
  }

  /** Step five: evaluate the selected rule against this exact value. */
  #applyPerValueEvaluation(request: RedactionRequest, state: StepState): RedactionOutcome | null {
    if (state.rule === null) {
      state.step = "per_value_evaluation";
      return null;
    }
    const check = checkSafeValueRule(state.rule, request.kind, request.value);
    if (check.accepted) {
      state.step = "per_value_evaluation";
      return null;
    }
    return this.#finish(
      "metadata_only",
      null,
      request.value.length,
      `${state.rule.rule_id}.${check.reason}`,
      "per_value_evaluation",
    );
  }

  /** Step seven: normalize, bound the length, bound cardinality, validate. */
  #applyNormalization(request: RedactionRequest, state: StepState): RedactionOutcome {
    const maxLength =
      state.rule === null ? MAX_SAFE_VALUE_LENGTH : Math.min(state.rule.max_length, MAX_SAFE_VALUE_LENGTH);
    const normalized = normalize(request.value, maxLength);
    if (normalized.length === 0) {
      return this.#finish(state.outputClass, "", request.value.length, state.ruleId, "normalization_and_limits");
    }
    if (state.outputClass === "pseudonymized") {
      return this.#finish(
        "pseudonymized",
        this.#pseudonymFor(state.ruleId, normalized),
        request.value.length,
        state.ruleId,
        "normalization_and_limits",
      );
    }
    if (!this.#admitCardinality(state.ruleId, normalized)) {
      return this.#finish(
        "metadata_only",
        null,
        request.value.length,
        `${state.ruleId}.cardinality_exceeded`,
        "normalization_and_limits",
      );
    }
    return this.#finish(state.outputClass, normalized, request.value.length, state.ruleId, "normalization_and_limits");
  }

  /**
   * Document-scoped pseudonym. The mapping lives only in this document's memory
   * and is never exported alongside the original value.
   */
  #pseudonymFor(ruleId: string, value: string): string {
    const key = `${ruleId}|${value}`;
    const existing = this.#pseudonyms.get(key);
    if (existing !== undefined) return existing;
    this.#pseudonymOrdinal += 1;
    const pseudonym = `psn_${String(this.#pseudonymOrdinal)}`;
    this.#pseudonyms.set(key, pseudonym);
    return pseudonym;
  }

  #admitCardinality(ruleId: string, value: string): boolean {
    const seen = this.#cardinality.get(ruleId) ?? new Set<string>();
    this.#cardinality.set(ruleId, seen);
    if (seen.has(value)) return true;
    if (seen.size >= MAX_RULE_CARDINALITY) return false;
    seen.add(value);
    return true;
  }

  #finish(
    outputClass: OutputClass,
    value: string | null,
    originalLength: number,
    ruleId: string,
    step: RedactionStep,
  ): RedactionOutcome {
    this.#audit.record({ step, rule_id: ruleId, output_class: outputClass });
    return {
      output_class: outputClass,
      value,
      length_bucket: lengthBucket(originalLength),
      rule_id: ruleId,
      decided_at_step: step,
    };
  }
}

/** Converts a pipeline outcome into the wire-level safe value. */
export const toSafeValue = (outcome: RedactionOutcome): SafeValue => ({
  output_class: outcome.output_class,
  value: outcome.value ?? undefined,
  length_bucket: outcome.length_bucket,
  rule_id: outcome.rule_id,
});
