/**
 * Declarative annotation attributes.
 *
 * The client annotation surface combines the typed SDK API with this small set
 * of `data-utrace-*` attributes. Unknown attribute names, unknown definition
 * ids, and malformed values fail closed: they are rejected and counted, never
 * turned into generic attributes or unrestricted payloads.
 */

import type { ManifestIndex } from "./manifest.js";

export const ANNOTATION_ATTRIBUTES = [
  "data-utrace-surface",
  "data-utrace-entity",
  "data-utrace-target",
  "data-utrace-visual-target",
  "data-utrace-sensitive",
  "data-utrace-safe-value",
  "data-utrace-milestone",
  "data-utrace-severity",
  "data-utrace-priority",
] as const;

export type AnnotationAttribute = (typeof ANNOTATION_ATTRIBUTES)[number];

const ATTRIBUTE_PREFIX = "data-utrace-";

export type AnnotationRejectionReason =
  | "unknown_attribute"
  | "unknown_definition"
  | "malformed_value"
  | "definition_not_visual"
  | "definition_visual_only";

export interface AnnotationRejection {
  readonly attribute: string;
  readonly reason: AnnotationRejectionReason;
}

export interface EntityAnnotation {
  readonly entity_definition_id: string;
  readonly instance_ref_raw: string;
  readonly instance_ref_rule_id: string;
}

export interface ElementAnnotations {
  readonly surface: string | null;
  readonly entity: EntityAnnotation | null;
  readonly targetDefinitionId: string | null;
  readonly visualTargetDefinitionId: string | null;
  readonly sensitive: boolean;
  readonly safeValueRuleId: string | null;
  readonly milestoneId: string | null;
  readonly severity: string | null;
  readonly priority: string | null;
  readonly rejections: readonly AnnotationRejection[];
}

const EMPTY: ElementAnnotations = {
  surface: null,
  entity: null,
  targetDefinitionId: null,
  visualTargetDefinitionId: null,
  sensitive: false,
  safeValueRuleId: null,
  milestoneId: null,
  severity: null,
  priority: null,
  rejections: [],
};

const isKnown = (name: string): name is AnnotationAttribute =>
  (ANNOTATION_ATTRIBUTES as readonly string[]).includes(name);

interface Draft {
  surface: string | null;
  entity: EntityAnnotation | null;
  targetDefinitionId: string | null;
  visualTargetDefinitionId: string | null;
  sensitive: boolean;
  safeValueRuleId: string | null;
  milestoneId: string | null;
  severity: string | null;
  priority: string | null;
}

const applyAttribute = (
  name: AnnotationAttribute,
  value: string,
  index: ManifestIndex,
  draft: Draft,
  rejections: AnnotationRejection[],
): void => {
  switch (name) {
    case "data-utrace-surface": {
      if (index.surface(value) === null) rejections.push({ attribute: name, reason: "unknown_definition" });
      else draft.surface = value;
      return;
    }
    case "data-utrace-entity": {
      const separator = value.indexOf(":");
      if (separator <= 0 || separator === value.length - 1) {
        rejections.push({ attribute: name, reason: "malformed_value" });
        return;
      }
      const definitionId = value.slice(0, separator);
      const definition = index.entity(definitionId);
      if (definition === null) {
        rejections.push({ attribute: name, reason: "unknown_definition" });
        return;
      }
      draft.entity = {
        entity_definition_id: definitionId,
        instance_ref_raw: value.slice(separator + 1),
        instance_ref_rule_id: definition.instance_ref_rule_id,
      };
      return;
    }
    case "data-utrace-target": {
      const definition = index.target(value);
      if (definition === null) rejections.push({ attribute: name, reason: "unknown_definition" });
      else if (definition.visual_only) rejections.push({ attribute: name, reason: "definition_visual_only" });
      else draft.targetDefinitionId = value;
      return;
    }
    case "data-utrace-visual-target": {
      const definition = index.target(value);
      if (definition === null) rejections.push({ attribute: name, reason: "unknown_definition" });
      else if (!definition.effects.includes("visual_guidance")) {
        rejections.push({ attribute: name, reason: "definition_not_visual" });
      } else draft.visualTargetDefinitionId = value;
      return;
    }
    case "data-utrace-sensitive": {
      if (value === "" || value === "true") draft.sensitive = true;
      else rejections.push({ attribute: name, reason: "malformed_value" });
      return;
    }
    case "data-utrace-safe-value": {
      const rule = index.manifest.redaction.safe_value_rules.find((candidate) => candidate.rule_id === value);
      if (rule === undefined) rejections.push({ attribute: name, reason: "unknown_definition" });
      else draft.safeValueRuleId = value;
      return;
    }
    case "data-utrace-milestone": {
      if (index.milestone(value) === null) rejections.push({ attribute: name, reason: "unknown_definition" });
      else draft.milestoneId = value;
      return;
    }
    case "data-utrace-severity": {
      if (!index.severityAllowed(value)) rejections.push({ attribute: name, reason: "unknown_definition" });
      else draft.severity = value;
      return;
    }
    case "data-utrace-priority": {
      if (!index.priorityAllowed(value)) rejections.push({ attribute: name, reason: "unknown_definition" });
      else draft.priority = value;
    }
  }
};

/** Reads one element's annotations against the activation's signed manifest. */
export const readElementAnnotations = (element: Element, index: ManifestIndex): ElementAnnotations => {
  const rejections: AnnotationRejection[] = [];
  const draft: Draft = {
    surface: null,
    entity: null,
    targetDefinitionId: null,
    visualTargetDefinitionId: null,
    sensitive: false,
    safeValueRuleId: null,
    milestoneId: null,
    severity: null,
    priority: null,
  };
  let seen = false;
  for (const attribute of element.attributes) {
    if (!attribute.name.startsWith(ATTRIBUTE_PREFIX)) continue;
    seen = true;
    if (!isKnown(attribute.name)) {
      rejections.push({ attribute: attribute.name, reason: "unknown_attribute" });
      continue;
    }
    applyAttribute(attribute.name, attribute.value, index, draft, rejections);
  }
  if (!seen) return EMPTY;
  return { ...draft, rejections };
};

/** Nearest annotated surface for an element, honouring sensitive surfaces. */
export const resolveSurface = (element: Element, index: ManifestIndex): { surface: string | undefined; sensitive: boolean } => {
  let current: Element | null = element;
  while (current !== null) {
    const value = current.getAttribute("data-utrace-surface");
    if (value !== null) {
      const definition = index.surface(value);
      if (definition !== null) return { surface: definition.surface_id, sensitive: definition.sensitive };
    }
    if (current.hasAttribute("data-utrace-sensitive")) return { surface: undefined, sensitive: true };
    current = current.parentElement;
  }
  return { surface: undefined, sensitive: false };
};
