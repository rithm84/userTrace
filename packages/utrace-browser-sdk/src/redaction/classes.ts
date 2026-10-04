/**
 * The five redaction output classes and the restriction order between them.
 *
 * Later pipeline steps may only restrict further. `restrictionRank` makes that
 * invariant checkable: a step may raise the rank, never lower it.
 */

export type OutputClass =
  | "never_collect"
  | "metadata_only"
  | "pseudonymized"
  | "bounded_safe_value"
  | "structural_operational_safe";

const RANK: Readonly<Record<OutputClass, number>> = {
  never_collect: 4,
  metadata_only: 3,
  pseudonymized: 2,
  bounded_safe_value: 1,
  structural_operational_safe: 0,
};

export const restrictionRank = (outputClass: OutputClass): number => RANK[outputClass];

/** Returns whichever of the two classes restricts more. */
export const mostRestrictive = (left: OutputClass, right: OutputClass): OutputClass =>
  RANK[left] >= RANK[right] ? left : right;

/** True when the class permits a literal value to be exported. */
export const carriesValue = (outputClass: OutputClass): boolean =>
  outputClass === "bounded_safe_value" ||
  outputClass === "structural_operational_safe" ||
  outputClass === "pseudonymized";

export type LengthBucket =
  | "empty"
  | "len_1_3"
  | "len_4_10"
  | "len_11_30"
  | "len_31_100"
  | "len_101_500"
  | "len_over_500";

export const lengthBucket = (length: number): LengthBucket => {
  if (length <= 0) return "empty";
  if (length <= 3) return "len_1_3";
  if (length <= 10) return "len_4_10";
  if (length <= 30) return "len_11_30";
  if (length <= 100) return "len_31_100";
  if (length <= 500) return "len_101_500";
  return "len_over_500";
};

/** Kind of value entering the pipeline. Drives which policy checks apply. */
export type ValueKind =
  | "user_text"
  | "form_value"
  | "accessible_label"
  | "field_name"
  | "route_param"
  | "operational"
  | "annotation_attribute";

/** The seven evaluation steps, in the order the contract fixes. */
export type RedactionStep =
  | "platform_exclusions"
  | "session_document_scope"
  | "signed_client_policy"
  | "structural_inheritance"
  | "per_value_evaluation"
  | "defensive_detection"
  | "normalization_and_limits";

export const REDACTION_STEPS: readonly RedactionStep[] = [
  "platform_exclusions",
  "session_document_scope",
  "signed_client_policy",
  "structural_inheritance",
  "per_value_evaluation",
  "defensive_detection",
  "normalization_and_limits",
];
