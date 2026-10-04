/**
 * Bootstrap environment contract.
 *
 * Only the prepared-preview runtime injects these variables. A production
 * startup profile injects none of them, so the package stays fully dormant:
 * no hooks, no provider, no control connection, no exporter.
 */

import { failureError, type ServerSdkError } from './errors.js';

/** Names of every environment variable the SDK reads. */
export const ENVIRONMENT_VARIABLES = {
  /**
   * One-time bootstrap credential injected by the prepared-environment runtime.
   * Its presence is the single signal that leaves dormancy. It is never stored
   * in the repository or in client configuration.
   */
  bootstrapCredential: 'UTRACE_SERVER_BOOTSTRAP_CREDENTIAL',
  /** Absolute `wss://` base URL of the control plane; it may carry a path prefix. */
  controlUrl: 'UTRACE_SERVER_CONTROL_URL',
  /** Absolute `https://` base URL of the telemetry ingestion edge; it may carry a path prefix. */
  ingestUrl: 'UTRACE_SERVER_INGEST_URL',
  /** Manifest `service_key` this process claims. */
  serviceName: 'UTRACE_SERVER_SERVICE_NAME',
  /**
   * Base64url JSON trust anchor for the signed instrumentation manifest,
   * shaped `{"key_id":"...","algorithm":"ed25519","public_key_spki_base64":"..."}`.
   * It arrives through the same runtime injection channel as the bootstrap
   * credential, so a repository checkout carries no verification material.
   */
  manifestTrustAnchor: 'UTRACE_SERVER_MANIFEST_TRUST_ANCHOR',
} as const;

/** Ed25519 public key trusted to sign the server instrumentation manifest. */
export interface ManifestTrustAnchor {
  readonly keyId: string;
  readonly algorithm: 'ed25519';
  readonly publicKeySpkiBase64: string;
}

/**
 * Validated bootstrap configuration for a prepared preview. Both URLs are base
 * URLs ending in `/`, so an endpoint path resolved against one keeps its prefix.
 */
export interface BootstrapEnvironment {
  readonly bootstrapCredential: string;
  readonly controlUrl: URL;
  readonly ingestUrl: URL;
  readonly serviceName: string;
  readonly manifestTrustAnchor: ManifestTrustAnchor;
}

/** Either a prepared-preview bootstrap, or dormancy. */
export type EnvironmentResolution =
  | { readonly kind: 'dormant' }
  | { readonly kind: 'prepared'; readonly environment: BootstrapEnvironment };

const SERVICE_KEY_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/u;

function readRequired(
  source: Readonly<Record<string, string | undefined>>,
  name: string,
  now: Date,
): string {
  const raw = source[name];
  if (raw === undefined || raw.trim() === '') {
    throw failureError(
      'A prepared-preview bootstrap credential is present but a required environment variable is missing.',
      {
        code: 'invalid_activation',
        retry: 'never',
        readiness_consequence: 'fail_stream',
        detail: { missing_variable: name },
      },
      now,
    );
  }
  return raw.trim();
}

function parseBaseUrl(
  value: string,
  name: string,
  expectedProtocols: readonly string[],
  now: Date,
): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw failureError('An SDK endpoint environment variable is not an absolute URL.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { variable: name },
    }, now);
  }
  if (!expectedProtocols.includes(parsed.protocol)) {
    throw failureError('An SDK endpoint environment variable uses an unexpected scheme.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { variable: name, scheme: parsed.protocol, expected: expectedProtocols.join(',') },
    }, now);
  }
  if (parsed.search !== '' || parsed.hash !== '') {
    throw failureError('An SDK endpoint environment variable must be a base URL without a query or fragment.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { variable: name },
    }, now);
  }
  if (!parsed.pathname.endsWith('/')) {
    parsed.pathname = `${parsed.pathname}/`;
  }
  return parsed;
}

function parseTrustAnchor(value: string, now: Date): ManifestTrustAnchor {
  const invalid = (reason: string): ServerSdkError =>
    failureError('The manifest trust anchor is not a usable Ed25519 public key.', {
      code: 'manifest_signature_invalid',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { variable: ENVIRONMENT_VARIABLES.manifestTrustAnchor, reason },
    }, now);

  let decoded: string;
  try {
    decoded = Buffer.from(value, 'base64url').toString('utf8');
  } catch {
    throw invalid('not_base64url');
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(decoded);
  } catch {
    throw invalid('not_json');
  }
  if (typeof parsed !== 'object' || parsed === null) {
    throw invalid('not_object');
  }
  const record = parsed as Record<string, unknown>;
  const keyId = record['key_id'];
  const algorithm = record['algorithm'];
  const publicKey = record['public_key_spki_base64'];
  if (typeof keyId !== 'string' || keyId === '') {
    throw invalid('missing_key_id');
  }
  if (algorithm !== 'ed25519') {
    throw invalid('unsupported_algorithm');
  }
  if (typeof publicKey !== 'string' || publicKey === '') {
    throw invalid('missing_public_key');
  }
  return { keyId, algorithm: 'ed25519', publicKeySpkiBase64: publicKey };
}

/**
 * Resolves the bootstrap environment.
 *
 * Without a bootstrap credential the result is `dormant` and no other variable
 * is read. With one, every remaining variable is required: a prepared preview
 * that cannot be observed must fail rather than boot as an unobserved
 * substitute.
 */
export function resolveEnvironment(
  source: Readonly<Record<string, string | undefined>>,
  now: Date,
): EnvironmentResolution {
  const credential = source[ENVIRONMENT_VARIABLES.bootstrapCredential];
  if (credential === undefined || credential.trim() === '') {
    return { kind: 'dormant' };
  }

  const serviceName = readRequired(source, ENVIRONMENT_VARIABLES.serviceName, now);
  if (!SERVICE_KEY_PATTERN.test(serviceName)) {
    throw failureError('The configured service name is not a valid manifest service key.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { variable: ENVIRONMENT_VARIABLES.serviceName },
    }, now);
  }

  return {
    kind: 'prepared',
    environment: {
      bootstrapCredential: credential.trim(),
      controlUrl: parseBaseUrl(
        readRequired(source, ENVIRONMENT_VARIABLES.controlUrl, now),
        ENVIRONMENT_VARIABLES.controlUrl,
        ['ws:', 'wss:'],
        now,
      ),
      ingestUrl: parseBaseUrl(
        readRequired(source, ENVIRONMENT_VARIABLES.ingestUrl, now),
        ENVIRONMENT_VARIABLES.ingestUrl,
        ['http:', 'https:'],
        now,
      ),
      serviceName,
      manifestTrustAnchor: parseTrustAnchor(
        readRequired(source, ENVIRONMENT_VARIABLES.manifestTrustAnchor, now),
        now,
      ),
    },
  };
}
