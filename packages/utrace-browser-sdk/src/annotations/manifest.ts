/**
 * Signed annotation manifest.
 *
 * Annotation definitions are built into a signed manifest associated with the
 * client release. The activation attestation pins its version and digest, so
 * application code may instantiate registered definitions but cannot invent one
 * or grant a new observation scope. Unknown definitions fail closed.
 */

import type { JsonObject, JsonValue } from "../core/json.js";
import {
  asObject,
  requireArray,
  requireBoolean,
  requireEnum,
  requireInteger,
  requireObject,
  requireString,
  requireStringArray,
  optionalString,
} from "../core/json.js";
import type { ValueKind } from "../redaction/classes.js";
import type { RedactionPolicy, SafeValueRule } from "../redaction/policy.js";

export type TargetEffect = "observe" | "visual_guidance";

export interface SurfaceDefinition {
  readonly surface_id: string;
  readonly sensitive: boolean;
}

export interface EntityDefinition {
  readonly entity_definition_id: string;
  readonly instance_ref_rule_id: string;
}

export interface TargetDefinition {
  readonly definition_id: string;
  readonly effects: readonly TargetEffect[];
  readonly detail_fields: readonly string[];
  readonly route_templates: readonly string[];
  readonly visual_only: boolean;
}

export interface MilestoneDefinition {
  readonly milestone_id: string;
  readonly attribute_rule_ids: readonly string[];
}

export interface AnnotationManifest {
  readonly manifest_version: string;
  readonly client_release_id: string;
  readonly surfaces: readonly SurfaceDefinition[];
  readonly entities: readonly EntityDefinition[];
  readonly targets: readonly TargetDefinition[];
  readonly visual_targets: readonly TargetDefinition[];
  readonly milestones: readonly MilestoneDefinition[];
  readonly severities: readonly string[];
  readonly priorities: readonly string[];
  readonly route_templates: readonly string[];
  /** Origins approved as server-SDK endpoints for W3C trace-context injection. */
  readonly approved_trace_origins: readonly string[];
  readonly redaction: RedactionPolicy;
}

const VALUE_KINDS: readonly ValueKind[] = [
  "user_text",
  "form_value",
  "accessible_label",
  "field_name",
  "route_param",
  "operational",
  "annotation_attribute",
];

const decodeSafeValueRule = (value: JsonValue, path: string): SafeValueRule => {
  const rule = asObject(value, path);
  const kinds = requireStringArray(rule, "allowed_kinds", path).map((kind, index) => {
    const match = VALUE_KINDS.find((candidate) => candidate === kind);
    if (match === undefined) {
      throw new Error(`uTrace manifest ${path}.allowed_kinds[${String(index)}] names unknown value kind ${kind}`);
    }
    return match;
  });
  const allowedValues = rule["allowed_values"];
  return {
    rule_id: requireString(rule, "rule_id", path),
    allowed_kinds: kinds,
    output_class: requireEnum(rule, "output_class", ["bounded_safe_value", "structural_operational_safe", "pseudonymized"] as const, path),
    max_length: requireInteger(rule, "max_length", path),
    allowed_values:
      allowedValues === undefined ? undefined : requireStringArray(rule, "allowed_values", path),
    allowed_pattern: optionalString(rule, "allowed_pattern", path) ?? undefined,
  };
};

const decodeRedactionPolicy = (source: JsonObject, path: string): RedactionPolicy => ({
  redaction_version: requireString(source, "redaction_version", path),
  policy_version: requireString(source, "policy_version", path),
  safe_value_rules: requireArray(source, "safe_value_rules", path).map((entry, index) =>
    decodeSafeValueRule(entry, `${path}.safe_value_rules[${String(index)}]`),
  ),
  sensitive_surfaces: requireStringArray(source, "sensitive_surfaces", path),
  sensitive_fields: requireStringArray(source, "sensitive_fields", path),
  default_user_value_class: "metadata_only",
});

const decodeTargetDefinition = (value: JsonValue, path: string, visualOnly: boolean): TargetDefinition => {
  const target = asObject(value, path);
  const effects = requireStringArray(target, "effects", path).map((effect, index) => {
    if (effect !== "observe" && effect !== "visual_guidance") {
      throw new Error(`uTrace manifest ${path}.effects[${String(index)}] names unknown effect ${effect}`);
    }
    return effect;
  });
  return {
    definition_id: requireString(target, "definition_id", path),
    effects,
    detail_fields: requireStringArray(target, "detail_fields", path),
    route_templates: requireStringArray(target, "route_templates", path),
    visual_only: visualOnly,
  };
};

/** Decodes a verified manifest payload. Any unknown or malformed field fails closed. */
export const decodeAnnotationManifest = (payload: JsonObject): AnnotationManifest => {
  const path = "annotation_manifest";
  return {
    manifest_version: requireString(payload, "manifest_version", path),
    client_release_id: requireString(payload, "client_release_id", path),
    surfaces: requireArray(payload, "surfaces", path).map((entry, index) => {
      const surface = asObject(entry, `${path}.surfaces[${String(index)}]`);
      return {
        surface_id: requireString(surface, "surface_id", `${path}.surfaces[${String(index)}]`),
        sensitive: requireBoolean(surface, "sensitive", `${path}.surfaces[${String(index)}]`),
      };
    }),
    entities: requireArray(payload, "entities", path).map((entry, index) => {
      const entity = asObject(entry, `${path}.entities[${String(index)}]`);
      return {
        entity_definition_id: requireString(entity, "entity_definition_id", `${path}.entities[${String(index)}]`),
        instance_ref_rule_id: requireString(entity, "instance_ref_rule_id", `${path}.entities[${String(index)}]`),
      };
    }),
    targets: requireArray(payload, "targets", path).map((entry, index) =>
      decodeTargetDefinition(entry, `${path}.targets[${String(index)}]`, false),
    ),
    visual_targets: requireArray(payload, "visual_targets", path).map((entry, index) =>
      decodeTargetDefinition(entry, `${path}.visual_targets[${String(index)}]`, true),
    ),
    milestones: requireArray(payload, "milestones", path).map((entry, index) => {
      const milestone = asObject(entry, `${path}.milestones[${String(index)}]`);
      return {
        milestone_id: requireString(milestone, "milestone_id", `${path}.milestones[${String(index)}]`),
        attribute_rule_ids: requireStringArray(milestone, "attribute_rule_ids", `${path}.milestones[${String(index)}]`),
      };
    }),
    severities: requireStringArray(payload, "severities", path),
    priorities: requireStringArray(payload, "priorities", path),
    route_templates: requireStringArray(payload, "route_templates", path),
    approved_trace_origins: requireStringArray(payload, "approved_trace_origins", path),
    redaction: decodeRedactionPolicy(requireObject(payload, "redaction", path), `${path}.redaction`),
  };
};

/** Indexed lookups over one activation's immutable manifest. */
export class ManifestIndex {
  readonly manifest: AnnotationManifest;
  readonly #targets = new Map<string, TargetDefinition>();
  readonly #surfaces = new Map<string, SurfaceDefinition>();
  readonly #entities = new Map<string, EntityDefinition>();
  readonly #milestones = new Map<string, MilestoneDefinition>();

  constructor(manifest: AnnotationManifest) {
    this.manifest = manifest;
    for (const target of [...manifest.targets, ...manifest.visual_targets]) {
      this.#targets.set(target.definition_id, target);
    }
    for (const surface of manifest.surfaces) this.#surfaces.set(surface.surface_id, surface);
    for (const entity of manifest.entities) this.#entities.set(entity.entity_definition_id, entity);
    for (const milestone of manifest.milestones) this.#milestones.set(milestone.milestone_id, milestone);
  }

  target(definitionId: string): TargetDefinition | null {
    return this.#targets.get(definitionId) ?? null;
  }

  surface(surfaceId: string): SurfaceDefinition | null {
    return this.#surfaces.get(surfaceId) ?? null;
  }

  entity(entityDefinitionId: string): EntityDefinition | null {
    return this.#entities.get(entityDefinitionId) ?? null;
  }

  milestone(milestoneId: string): MilestoneDefinition | null {
    return this.#milestones.get(milestoneId) ?? null;
  }

  severityAllowed(severity: string): boolean {
    return this.manifest.severities.includes(severity);
  }

  priorityAllowed(priority: string): boolean {
    return this.manifest.priorities.includes(priority);
  }

  /** True for the document origin or an origin the manifest approved as a server-SDK endpoint. */
  traceInjectionAllowed(targetOrigin: string, documentOrigin: string): boolean {
    return targetOrigin === documentOrigin || this.manifest.approved_trace_origins.includes(targetOrigin);
  }
}
