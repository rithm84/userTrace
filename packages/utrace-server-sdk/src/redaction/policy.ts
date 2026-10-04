/**
 * The compiled redaction policy for one activation.
 *
 * It is derived once from the verified manifest so the per-span path performs
 * lookups rather than re-deriving policy on every finished span.
 */

import {
  ADAPTER_SCOPE_NAMES,
  ADAPTER_SUPPRESSED_SCOPE_NAMES,
  ANNOTATION_SCOPE_NAME,
} from '../constants.js';
import type { ActiveObservation } from '../activation.js';
import type {
  AnnotationDefinition,
  NetworkTarget,
  OperationalNoise,
  RedactionRules,
  RegisteredRoute,
} from '../contracts/manifest.js';

/** Immutable policy inputs the redaction pipeline consults. */
export interface RedactionPolicy {
  readonly routes: readonly RegisteredRoute[];
  readonly operationalNoise: readonly OperationalNoise[];
  readonly registeredOperations: ReadonlySet<string>;
  readonly propagationTargets: readonly NetworkTarget[];
  readonly allowedHttpDestinations: readonly NetworkTarget[];
  readonly allowedDatabaseClasses: ReadonlySet<string>;
  readonly allowedCacheClasses: ReadonlySet<string>;
  readonly allowedQueueDestinations: ReadonlySet<string>;
  readonly allowedErrorTypes: ReadonlySet<string>;
  readonly sanitizedErrorMessages: ReadonlyMap<string, string>;
  readonly definitionsByRef: ReadonlyMap<string, AnnotationDefinition>;
  readonly definitionsByName: ReadonlyMap<string, AnnotationDefinition>;
  readonly eligibleScopes: ReadonlySet<string>;
  /** Framework scopes whose spans are dropped rather than failed. */
  readonly suppressedScopes: ReadonlySet<string>;
  readonly repositoryRoot: string;
  readonly maxStackFrames: number;
  readonly maxAttributeValueLength: number;
  readonly maxSpanAttributeCount: number;
  readonly productArea: string;
}

function rules(observation: ActiveObservation): RedactionRules {
  return observation.manifest.redaction_rules;
}

/** Compiles the activation's manifest into a redaction policy. */
export function compileRedactionPolicy(observation: ActiveObservation): RedactionPolicy {
  const manifestRules = rules(observation);
  const eligibleScopes = new Set<string>([ANNOTATION_SCOPE_NAME]);
  const suppressedScopes = new Set<string>();
  for (const adapter of observation.service.required_adapters) {
    eligibleScopes.add(ADAPTER_SCOPE_NAMES[adapter]);
    for (const scope of ADAPTER_SUPPRESSED_SCOPE_NAMES[adapter] ?? []) {
      suppressedScopes.add(scope);
    }
  }

  return {
    routes: observation.service.registered_routes,
    operationalNoise: observation.service.operational_noise,
    registeredOperations: new Set(observation.service.registered_operations),
    propagationTargets: observation.manifest.propagation_targets,
    allowedHttpDestinations: manifestRules.allowed_http_destinations,
    allowedDatabaseClasses: new Set(manifestRules.allowed_database_classes),
    allowedCacheClasses: new Set(manifestRules.allowed_cache_classes),
    allowedQueueDestinations: new Set(manifestRules.allowed_queue_destinations),
    allowedErrorTypes: new Set(manifestRules.allowed_error_types),
    sanitizedErrorMessages: new Map(
      manifestRules.sanitized_error_messages.map((rule) => [rule.error_type, rule.sanitized_message]),
    ),
    definitionsByRef: new Map(
      observation.manifest.annotation_definitions.map((definition) => [
        definition.definition_ref,
        definition,
      ]),
    ),
    definitionsByName: new Map(
      observation.manifest.annotation_definitions.map((definition) => [
        definition.name,
        definition,
      ]),
    ),
    eligibleScopes,
    suppressedScopes,
    repositoryRoot: manifestRules.repository_root,
    maxStackFrames: manifestRules.max_stack_frames,
    maxAttributeValueLength: manifestRules.max_attribute_value_length,
    maxSpanAttributeCount: manifestRules.max_span_attribute_count,
    productArea: observation.service.service_key,
  };
}
