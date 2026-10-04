/**
 * Builds and signs server instrumentation manifests for the tests.
 *
 * The signer canonicalizes independently of the SDK's implementation, so a
 * disagreement between them fails a test rather than passing silently.
 */

import { generateKeyPairSync, randomUUID, sign as signBytes } from 'node:crypto';

import type {
  Adapter,
  AnnotationDefinition,
  NetworkTarget,
  OperationalNoise,
  RegisteredRoute,
  ServerInstrumentationManifest,
  SignedServerInstrumentationManifest,
} from '../../src/contracts/manifest.js';

/** An Ed25519 signing identity plus the env-encoded trust anchor. */
export interface SigningIdentity {
  readonly keyId: string;
  readonly privateKeyPem: string;
  readonly trustAnchorEnvValue: string;
  readonly publicKeySpkiBase64: string;
}

/** Creates a fresh Ed25519 identity and its trust anchor env value. */
export function createSigningIdentity(keyId: string): SigningIdentity {
  const { publicKey, privateKey } = generateKeyPairSync('ed25519');
  const spki = publicKey.export({ format: 'der', type: 'spki' });
  const publicKeySpkiBase64 = Buffer.from(spki).toString('base64');
  const anchor = JSON.stringify({
    key_id: keyId,
    algorithm: 'ed25519',
    public_key_spki_base64: publicKeySpkiBase64,
  });
  return {
    keyId,
    privateKeyPem: privateKey.export({ format: 'pem', type: 'pkcs8' }).toString(),
    trustAnchorEnvValue: Buffer.from(anchor, 'utf8').toString('base64url'),
    publicKeySpkiBase64,
  };
}

function canonicalize(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalize).join(',')}]`;
  }
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const members = Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(record[key])}`);
    return `{${members.join(',')}}`;
  }
  return JSON.stringify(value);
}

/** Signs a manifest with the identity's private key. */
export function signManifest(
  manifest: ServerInstrumentationManifest,
  identity: SigningIdentity,
): SignedServerInstrumentationManifest {
  const payload = Buffer.from(canonicalize(manifest), 'utf8');
  const signature = signBytes(null, payload, identity.privateKeyPem);
  return {
    manifest,
    signature: {
      key_id: identity.keyId,
      algorithm: 'ed25519',
      value: Buffer.from(signature).toString('base64'),
    },
  };
}

/** What a test wants in the manifest for one service. */
export interface ManifestServiceOverrides {
  readonly serviceKey: string;
  readonly serviceName?: string;
  readonly requiredAdapters: readonly Adapter[];
  readonly registeredRoutes: readonly RegisteredRoute[];
  readonly registeredOperations?: readonly string[];
  readonly operationalNoise?: readonly OperationalNoise[];
}

/** What a test wants in the manifest overall. */
export interface ManifestOverrides {
  readonly services: readonly ManifestServiceOverrides[];
  readonly propagationTargets?: readonly NetworkTarget[];
  readonly allowedHttpDestinations?: readonly NetworkTarget[];
  readonly allowedDatabaseClasses?: readonly string[];
  readonly allowedCacheClasses?: readonly string[];
  readonly allowedQueueDestinations?: readonly string[];
  readonly allowedErrorTypes?: readonly string[];
  readonly sanitizedErrorMessages?: readonly { error_type: string; sanitized_message: string }[];
  readonly repositoryRoot?: string;
  readonly annotationDefinitions?: readonly AnnotationDefinition[];
}

/** Builds a complete, valid manifest from the overrides a test supplies. */
export function buildManifest(overrides: ManifestOverrides): ServerInstrumentationManifest {
  return {
    manifest_id: randomUUID(),
    client_id: randomUUID(),
    client_release_id: 'shopalytics-release-1',
    manifest_version: 1,
    policy_version: '1.0.0',
    utrace_schema_version: '1.0.0',
    semantic_convention_version: '1.37.0',
    server_sdk_contract_major: 1,
    services: overrides.services.map((service) => ({
      service_key: service.serviceKey,
      service_name: service.serviceName ?? 'shopalytics-api',
      service_namespace: 'shopalytics',
      service_version: '1.4.2',
      required: true,
      required_adapters: [...service.requiredAdapters],
      registered_routes: [...service.registeredRoutes],
      registered_operations: [...(service.registeredOperations ?? [])],
      operational_noise: [...(service.operationalNoise ?? [])],
    })),
    propagation_targets: [...(overrides.propagationTargets ?? [])],
    redaction_rules: {
      allowed_http_destinations: [...(overrides.allowedHttpDestinations ?? [])],
      allowed_database_classes: [...(overrides.allowedDatabaseClasses ?? [])],
      allowed_cache_classes: [...(overrides.allowedCacheClasses ?? [])],
      allowed_queue_destinations: [...(overrides.allowedQueueDestinations ?? [])],
      allowed_error_types: [...(overrides.allowedErrorTypes ?? [])],
      sanitized_error_messages: [...(overrides.sanitizedErrorMessages ?? [])],
      repository_root: overrides.repositoryRoot ?? process.cwd(),
      max_stack_frames: 12,
      max_attribute_value_length: 256,
      max_span_attribute_count: 32,
    },
    annotation_definitions: [...(overrides.annotationDefinitions ?? [])],
  };
}
