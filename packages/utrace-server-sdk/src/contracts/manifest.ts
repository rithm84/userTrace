/**
 * Runtime validators for the signed server instrumentation manifest.
 *
 * These bindings mirror
 * `packages/utrace-contracts/schemas/server-activation/server-instrumentation-manifest.v1.schema.json`
 * and `.../signed-server-instrumentation-manifest.v1.schema.json`. They live in
 * this package until the contracts workstream publishes generated bindings.
 */

import * as z from 'zod';

const versionTriple = z.string().regex(/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/u);
const identifier = z.string().max(120).regex(/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/u);
const routeTemplate = z
  .string()
  .max(200)
  .regex(/^\/([A-Za-z0-9._~-]+|:[a-z][a-z0-9_]*|\*)?(\/([A-Za-z0-9._~-]+|:[a-z][a-z0-9_]*|\*))*$/u);

export const HTTP_METHODS = [
  'GET',
  'HEAD',
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
  'OPTIONS',
  'TRACE',
  'CONNECT',
] as const;

export const ADAPTERS = ['node_http', 'undici', 'pg', 'express', 'fastify', 'next_server'] as const;

export type HttpMethod = (typeof HTTP_METHODS)[number];
export type Adapter = (typeof ADAPTERS)[number];

const httpMethodSchema = z.enum(HTTP_METHODS);
const adapterSchema = z.enum(ADAPTERS);

export const networkTargetSchema = z.strictObject({
  scheme: z.enum(['http', 'https']),
  host: z.string().min(1).max(253),
  port: z.int().min(1).max(65_535),
});

export const registeredRouteSchema = z.strictObject({
  route_template: routeTemplate,
  methods: z.array(httpMethodSchema).min(1).max(9),
  operation_name: identifier,
  required: z.boolean(),
});

export const operationalNoiseSchema = z.strictObject({
  path_pattern: routeTemplate,
  methods: z.array(httpMethodSchema).min(1).max(9),
});

export const manifestServiceSchema = z.strictObject({
  service_key: identifier,
  service_name: z.string().min(1).max(120),
  service_namespace: z.string().min(1).max(120),
  service_version: z.string().min(1).max(120),
  required: z.boolean(),
  required_adapters: z.array(adapterSchema).min(1).max(6),
  registered_routes: z.array(registeredRouteSchema).max(512),
  registered_operations: z.array(identifier).max(512),
  operational_noise: z.array(operationalNoiseSchema).max(128),
});

export const redactionRulesSchema = z.strictObject({
  allowed_http_destinations: z.array(networkTargetSchema).max(128),
  allowed_database_classes: z.array(z.string().min(1).max(200)).max(512),
  allowed_cache_classes: z.array(z.string().min(1).max(200)).max(256),
  allowed_queue_destinations: z.array(z.string().min(1).max(200)).max(256),
  allowed_error_types: z.array(z.string().min(1).max(200)).max(512),
  sanitized_error_messages: z
    .array(
      z.strictObject({
        error_type: z.string().min(1).max(200),
        sanitized_message: z.string().min(1).max(200),
      }),
    )
    .max(512),
  repository_root: z.string().min(1).max(1024),
  max_stack_frames: z.int().min(0).max(32),
  max_attribute_value_length: z.int().min(1).max(2048),
  max_span_attribute_count: z.int().min(1).max(128),
});

export const ANNOTATION_KINDS = [
  'product_operation',
  'workflow_milestone',
  'entity_class',
  'safe_value',
  'expected_outcome',
  'background_root',
] as const;

export const OUTPUT_CLASSES = [
  'registered_reference',
  'bounded_enum',
  'bounded_integer',
  'metadata_only',
] as const;

export const annotationDefinitionSchema = z
  .strictObject({
    definition_id: z.uuid(),
    definition_ref: z.string().max(120).regex(/^[a-z][a-z0-9_]*\.v[1-9][0-9]*$/u),
    kind: z.enum(ANNOTATION_KINDS),
    name: identifier,
    output_class: z.enum(OUTPUT_CLASSES),
    permitted_values: z.array(z.string().min(1).max(120)).min(1).max(64).optional(),
    value_bounds: z.strictObject({ minimum: z.int(), maximum: z.int() }).optional(),
  })
  .refine(
    (definition) =>
      (definition.output_class === 'bounded_enum') === (definition.permitted_values !== undefined),
    { error: 'permitted_values is required exactly when output_class is bounded_enum' },
  )
  .refine(
    (definition) =>
      (definition.output_class === 'bounded_integer') === (definition.value_bounds !== undefined),
    { error: 'value_bounds is required exactly when output_class is bounded_integer' },
  );

export const serverInstrumentationManifestSchema = z.strictObject({
  manifest_id: z.uuid(),
  client_id: z.uuid(),
  client_release_id: z.string().min(1).max(200),
  manifest_version: z.int().min(1),
  policy_version: versionTriple,
  utrace_schema_version: versionTriple,
  semantic_convention_version: versionTriple,
  server_sdk_contract_major: z.int().min(1),
  services: z.array(manifestServiceSchema).min(1).max(64),
  propagation_targets: z.array(networkTargetSchema).max(128),
  redaction_rules: redactionRulesSchema,
  annotation_definitions: z.array(annotationDefinitionSchema).max(512),
});

export const signedServerInstrumentationManifestSchema = z.strictObject({
  manifest: serverInstrumentationManifestSchema,
  signature: z.strictObject({
    key_id: z.string().min(1).max(120),
    algorithm: z.literal('ed25519'),
    value: z.string().max(512).regex(/^[A-Za-z0-9+/]+={0,2}$/u),
  }),
});

export type NetworkTarget = z.infer<typeof networkTargetSchema>;
export type RegisteredRoute = z.infer<typeof registeredRouteSchema>;
export type OperationalNoise = z.infer<typeof operationalNoiseSchema>;
export type ManifestService = z.infer<typeof manifestServiceSchema>;
export type RedactionRules = z.infer<typeof redactionRulesSchema>;
export type AnnotationDefinition = z.infer<typeof annotationDefinitionSchema>;
export type ServerInstrumentationManifest = z.infer<typeof serverInstrumentationManifestSchema>;
export type SignedServerInstrumentationManifest = z.infer<
  typeof signedServerInstrumentationManifestSchema
>;
