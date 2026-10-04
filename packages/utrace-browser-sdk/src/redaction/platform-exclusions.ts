/**
 * Step one: non-overridable platform exclusions.
 *
 * These cover credentials, authentication material, payment details, private
 * keys, security answers, unrestricted request and response bodies, and
 * browser-owned or cross-origin content outside the authenticated runtime. No
 * client policy can re-enable any of them, so this module takes no policy input.
 */

import type { ValueKind } from "./classes.js";

export interface PlatformExclusionContext {
  readonly kind: ValueKind;
  /** Lowercased element tag name, when the value came from an element. */
  readonly tagName: string | null;
  /** Lowercased `type` attribute of an input element. */
  readonly inputType: string | null;
  /** Lowercased `autocomplete` token list of the element. */
  readonly autocomplete: readonly string[];
  /** Lowercased `name` and `id` attributes of the element. */
  readonly identifiers: readonly string[];
  /** True when the value originates outside the authenticated document. */
  readonly foreignOrigin: boolean;
  /** True when the value is a raw request or response body. */
  readonly rawBody: boolean;
}

const EXCLUDED_INPUT_TYPES: readonly string[] = ["password", "hidden"];

const EXCLUDED_AUTOCOMPLETE: readonly string[] = [
  "current-password",
  "new-password",
  "one-time-code",
  "cc-number",
  "cc-exp",
  "cc-exp-month",
  "cc-exp-year",
  "cc-csc",
  "cc-type",
  "cc-name",
  "cc-given-name",
  "cc-family-name",
];

const EXCLUDED_IDENTIFIER_PATTERNS: readonly RegExp[] = [
  /pass(word|phrase)/,
  /\bpwd\b/,
  /secret/,
  /api[-_]?key/,
  /access[-_]?token/,
  /refresh[-_]?token/,
  /bearer/,
  /session[-_]?id/,
  /private[-_]?key/,
  /credit[-_]?card/,
  /\bcvv\b|\bcvc\b/,
  /card[-_]?number/,
  /routing[-_]?number/,
  /account[-_]?number/,
  /security[-_]?(question|answer)/,
  /mother.?s?[-_]?maiden/,
  /\bssn\b|social[-_]?security/,
  /otp|totp|mfa[-_]?code/,
];

export interface PlatformExclusionResult {
  readonly excluded: boolean;
  readonly rule_id: string;
}

const NOT_EXCLUDED: PlatformExclusionResult = { excluded: false, rule_id: "platform.none" };

/**
 * Decides whether a value is permanently excluded. Exclusion is structural: it
 * inspects the element's own identity, never the value's content.
 */
export const evaluatePlatformExclusions = (context: PlatformExclusionContext): PlatformExclusionResult => {
  if (context.foreignOrigin) return { excluded: true, rule_id: "platform.foreign_origin" };
  if (context.rawBody) return { excluded: true, rule_id: "platform.raw_body" };
  if (context.inputType !== null && EXCLUDED_INPUT_TYPES.includes(context.inputType)) {
    return { excluded: true, rule_id: `platform.input_type.${context.inputType}` };
  }
  for (const token of context.autocomplete) {
    if (EXCLUDED_AUTOCOMPLETE.includes(token)) {
      return { excluded: true, rule_id: `platform.autocomplete.${token}` };
    }
  }
  for (const identifier of context.identifiers) {
    for (const pattern of EXCLUDED_IDENTIFIER_PATTERNS) {
      if (pattern.test(identifier)) {
        return { excluded: true, rule_id: `platform.identifier.${pattern.source}` };
      }
    }
  }
  return NOT_EXCLUDED;
};

/** True when the element may never become an automatic visual-only target. */
export const isPlatformExcludedElement = (element: Element): boolean => {
  const tagName = element.tagName.toLowerCase();
  const inputType = tagName === "input" ? (element.getAttribute("type") ?? "text").toLowerCase() : null;
  return evaluatePlatformExclusions({
    kind: "form_value",
    tagName,
    inputType,
    autocomplete: (element.getAttribute("autocomplete") ?? "").toLowerCase().split(/\s+/).filter(Boolean),
    identifiers: [element.getAttribute("name") ?? "", element.getAttribute("id") ?? ""].filter(Boolean),
    foreignOrigin: false,
    rawBody: false,
  }).excluded;
};
