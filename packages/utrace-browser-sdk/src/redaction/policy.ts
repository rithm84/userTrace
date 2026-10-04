/**
 * Step three input: the signed client annotation and redaction policy.
 *
 * The policy arrives inside the signed annotation manifest and is immutable for
 * one activation. It can only narrow what the platform already permits: a rule
 * that names an excluded field has no effect, because step one runs first.
 */

import type { OutputClass, ValueKind } from "./classes.js";

export interface SafeValueRule {
  readonly rule_id: string;
  readonly allowed_kinds: readonly ValueKind[];
  readonly output_class: Extract<OutputClass, "bounded_safe_value" | "structural_operational_safe" | "pseudonymized">;
  readonly max_length: number;
  /** Optional closed set. When present, a value outside it is rejected. */
  readonly allowed_values?: readonly string[] | undefined;
  /** Optional anchored pattern the value must match in full. */
  readonly allowed_pattern?: string | undefined;
}

export interface RedactionPolicy {
  readonly redaction_version: string;
  readonly policy_version: string;
  readonly safe_value_rules: readonly SafeValueRule[];
  /** Surfaces whose subtree is sensitive; everything inside inherits taint. */
  readonly sensitive_surfaces: readonly string[];
  /** Annotated sensitive field ids; their values never leave as literals. */
  readonly sensitive_fields: readonly string[];
  /** Default class for user-authored text and form values with no exact rule. */
  readonly default_user_value_class: Extract<OutputClass, "metadata_only">;
}

export const findSafeValueRule = (policy: RedactionPolicy, ruleId: string): SafeValueRule | null =>
  policy.safe_value_rules.find((rule) => rule.rule_id === ruleId) ?? null;

export interface RuleCheck {
  readonly accepted: boolean;
  readonly reason: "ok" | "kind_not_allowed" | "value_not_allowed" | "pattern_mismatch" | "too_long";
}

/** Exact rule evaluation. A rule that does not match fails closed. */
export const checkSafeValueRule = (rule: SafeValueRule, kind: ValueKind, value: string): RuleCheck => {
  if (!rule.allowed_kinds.includes(kind)) return { accepted: false, reason: "kind_not_allowed" };
  if (value.length > rule.max_length) return { accepted: false, reason: "too_long" };
  if (rule.allowed_values !== undefined && !rule.allowed_values.includes(value)) {
    return { accepted: false, reason: "value_not_allowed" };
  }
  if (rule.allowed_pattern !== undefined && !new RegExp(`^(?:${rule.allowed_pattern})$`, "u").test(value)) {
    return { accepted: false, reason: "pattern_mismatch" };
  }
  return { accepted: true, reason: "ok" };
};
