/**
 * Step six: defensive sensitive-data detection.
 *
 * Detection runs on a value that earlier steps already allowed. It may only
 * restrict further, and it never records or returns the inspected value.
 */

import type { OutputClass } from "./classes.js";

export interface DetectionResult {
  readonly restrictTo: OutputClass | null;
  readonly rule_id: string;
}

const CLEAN: DetectionResult = { restrictTo: null, rule_id: "detector.none" };

interface Detector {
  readonly rule_id: string;
  readonly pattern: RegExp;
  readonly restrictTo: OutputClass;
}

const DETECTORS: readonly Detector[] = [
  { rule_id: "detector.email", pattern: /[\w.+-]+@[\w-]+\.[\w.-]+/, restrictTo: "pseudonymized" },
  { rule_id: "detector.long_digit_run", pattern: /\d[\d ._-]{11,}/, restrictTo: "metadata_only" },
  { rule_id: "detector.jwt", pattern: /\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}/, restrictTo: "never_collect" },
  { rule_id: "detector.private_key_block", pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/, restrictTo: "never_collect" },
  { rule_id: "detector.bearer_token", pattern: /\b(?:bearer|token|secret|api[_-]?key)\b[\s:=]+\S{8,}/i, restrictTo: "never_collect" },
  { rule_id: "detector.credit_card_shape", pattern: /\b(?:\d[ -]?){13,19}\b/, restrictTo: "never_collect" },
  { rule_id: "detector.us_ssn_shape", pattern: /\b\d{3}-\d{2}-\d{4}\b/, restrictTo: "never_collect" },
  { rule_id: "detector.url_credentials", pattern: /https?:\/\/[^\s/@]+:[^\s/@]+@/, restrictTo: "never_collect" },
];

/** Returns the most restrictive detector hit, or a clean result. */
export const detectSensitiveContent = (value: string): DetectionResult => {
  let hit: DetectionResult = CLEAN;
  for (const detector of DETECTORS) {
    if (!detector.pattern.test(value)) continue;
    if (detector.restrictTo === "never_collect") return { restrictTo: "never_collect", rule_id: detector.rule_id };
    if (hit.restrictTo === null) hit = { restrictTo: detector.restrictTo, rule_id: detector.rule_id };
  }
  return hit;
};
