/**
 * Validation of the versioned `utrace.*` namespace.
 *
 * A `utrace.*` attribute is exportable only when it instantiates a registered
 * annotation definition with a value inside that definition's output class.
 * Unknown keys, unknown names, and out-of-class values fail the SDK contract.
 */

import type { AttributeValue, Attributes } from '@opentelemetry/api';

import { UTRACE_ATTRIBUTES, UTRACE_ATTRIBUTE_PREFIX } from '../constants.js';
import type { AnnotationDefinition } from '../contracts/manifest.js';
import type { ServerSdkFailureInput } from '../errors.js';
import type { RedactionPolicy } from './policy.js';

/** Result of validating the `utrace.*` attributes of one span. */
export type UtraceAttributeOutcome =
  | { readonly kind: 'retain'; readonly attributes: Attributes }
  | { readonly kind: 'fail'; readonly failure: ServerSdkFailureInput };

interface PairedAnnotation {
  readonly nameKey: string;
  readonly refKey: string;
  readonly kinds: readonly AnnotationDefinition['kind'][];
}

const PAIRED_ANNOTATIONS: readonly PairedAnnotation[] = [
  {
    nameKey: UTRACE_ATTRIBUTES.operationName,
    refKey: UTRACE_ATTRIBUTES.operationDefinitionRef,
    kinds: ['product_operation', 'background_root'],
  },
  {
    nameKey: UTRACE_ATTRIBUTES.milestoneName,
    refKey: UTRACE_ATTRIBUTES.milestoneDefinitionRef,
    kinds: ['workflow_milestone'],
  },
  {
    nameKey: UTRACE_ATTRIBUTES.entityClass,
    refKey: UTRACE_ATTRIBUTES.entityDefinitionRef,
    kinds: ['entity_class'],
  },
  {
    nameKey: UTRACE_ATTRIBUTES.outcomeName,
    refKey: UTRACE_ATTRIBUTES.outcomeDefinitionRef,
    kinds: ['expected_outcome'],
  },
];

const PLATFORM_OWNED_KEYS: ReadonlySet<string> = new Set([
  UTRACE_ATTRIBUTES.errorFingerprint,
  UTRACE_ATTRIBUTES.errorProductArea,
  UTRACE_ATTRIBUTES.errorFrames,
  UTRACE_ATTRIBUTES.errorMessage,
  UTRACE_ATTRIBUTES.cacheClass,
  UTRACE_ATTRIBUTES.cacheHit,
]);

function schemaFailure(detail: Record<string, string | number | boolean>): ServerSdkFailureInput {
  return {
    code: 'redaction_or_schema_failure',
    retry: 'never',
    readiness_consequence: 'withdraw_readiness',
    detail,
  };
}

function valueMatchesOutputClass(
  definition: AnnotationDefinition,
  value: AttributeValue,
): boolean {
  switch (definition.output_class) {
    case 'metadata_only':
      return value === true;
    case 'registered_reference':
      return typeof value === 'string' && value === definition.name;
    case 'bounded_enum':
      return typeof value === 'string' && (definition.permitted_values ?? []).includes(value);
    case 'bounded_integer':
      return (
        typeof value === 'number'
        && Number.isInteger(value)
        && definition.value_bounds !== undefined
        && value >= definition.value_bounds.minimum
        && value <= definition.value_bounds.maximum
      );
    default:
      return false;
  }
}

function validatePair(
  attributes: Attributes,
  pair: PairedAnnotation,
  policy: RedactionPolicy,
  retained: Attributes,
): ServerSdkFailureInput | undefined {
  const name = attributes[pair.nameKey];
  const ref = attributes[pair.refKey];
  if (name === undefined && ref === undefined) {
    return undefined;
  }
  if (typeof name !== 'string' || typeof ref !== 'string') {
    return schemaFailure({ attribute: pair.nameKey, reason: 'incomplete_annotation_pair' });
  }
  const definition = policy.definitionsByRef.get(ref);
  if (definition === undefined) {
    return schemaFailure({ attribute: pair.refKey, reason: 'unknown_definition_ref' });
  }
  if (definition.name !== name) {
    return schemaFailure({ attribute: pair.nameKey, reason: 'definition_name_mismatch' });
  }
  if (!pair.kinds.includes(definition.kind)) {
    return schemaFailure({ attribute: pair.nameKey, reason: 'definition_kind_mismatch' });
  }
  retained[pair.nameKey] = name;
  retained[pair.refKey] = ref;
  return undefined;
}

function validateSafeValue(
  key: string,
  value: AttributeValue | undefined,
  policy: RedactionPolicy,
  retained: Attributes,
): ServerSdkFailureInput | undefined {
  const name = key.slice(UTRACE_ATTRIBUTES.valuePrefix.length);
  const definition = policy.definitionsByName.get(name);
  if (definition === undefined || definition.kind !== 'safe_value') {
    return schemaFailure({ attribute: key, reason: 'unknown_safe_value_definition' });
  }
  if (value === undefined || !valueMatchesOutputClass(definition, value)) {
    return schemaFailure({ attribute: key, reason: 'value_outside_output_class' });
  }
  retained[key] = value;
  return undefined;
}

/**
 * Validates and returns every exportable `utrace.*` attribute on a span.
 *
 * Platform-owned keys, such as redacted error evidence, are produced by the SDK
 * itself and pass through; anything else must resolve to a manifest definition.
 */
export function redactUtraceAttributes(
  attributes: Attributes,
  policy: RedactionPolicy,
): UtraceAttributeOutcome {
  const retained: Attributes = {};

  for (const pair of PAIRED_ANNOTATIONS) {
    const failure = validatePair(attributes, pair, policy, retained);
    if (failure !== undefined) {
      return { kind: 'fail', failure };
    }
  }

  for (const key of Object.keys(attributes)) {
    if (!key.startsWith(UTRACE_ATTRIBUTE_PREFIX)) {
      continue;
    }
    if (PLATFORM_OWNED_KEYS.has(key) || Object.prototype.hasOwnProperty.call(retained, key)) {
      continue;
    }
    if (key.startsWith(UTRACE_ATTRIBUTES.valuePrefix)) {
      const failure = validateSafeValue(key, attributes[key], policy, retained);
      if (failure !== undefined) {
        return { kind: 'fail', failure };
      }
      continue;
    }
    const isPairMember = PAIRED_ANNOTATIONS.some(
      (pair) => pair.nameKey === key || pair.refKey === key,
    );
    if (isPairMember) {
      continue;
    }
    return { kind: 'fail', failure: schemaFailure({ attribute: key, reason: 'unknown_utrace_attribute' }) };
  }

  return { kind: 'retain', attributes: retained };
}
