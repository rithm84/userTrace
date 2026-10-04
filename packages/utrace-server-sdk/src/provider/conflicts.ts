/**
 * Conflict detection before uTrace takes ownership.
 *
 * In a prepared preview uTrace owns the sole provider, context manager,
 * propagator, sampler, instrumentation set, processors, and exporter. A second
 * provider, a duplicate instrumentation hook, a conflicting preload, or a
 * non-uTrace exporter is an activation failure: the SDK does not choose one or
 * merge the resulting telemetry.
 */

import { Server as HttpServer } from 'node:http';

import type { ServerSdkFailureInput } from '../errors.js';

/** One detected conflict, ready to be raised as a typed failure. */
export type ConflictFinding = ServerSdkFailureInput;

/** Environment variables that would configure a non-uTrace exporter. */
const CONFLICTING_ENVIRONMENT_PREFIXES = [
  'OTEL_TRACES_EXPORTER',
  'OTEL_EXPORTER_OTLP_',
  'OTEL_TRACES_SAMPLER',
  'OTEL_PROPAGATORS',
  'OTEL_SDK_DISABLED',
];

/** Preload markers belonging to another tracing distribution. */
const CONFLICTING_PRELOAD_MARKERS = [
  '@opentelemetry/auto-instrumentations-node',
  '@opentelemetry/sdk-node',
  'dd-trace',
  'newrelic',
  'elastic-apm-node',
  '@sentry/node',
];

interface PossiblyWrapped {
  __wrapped?: boolean;
}

function duplicateHook(detail: Record<string, string | number | boolean>): ConflictFinding {
  return {
    code: 'duplicate_provider_or_hook',
    retry: 'never',
    readiness_consequence: 'fail_stream',
    detail,
  };
}

/**
 * Inspects the process before installation.
 *
 * Returns every conflict found; an empty array means uTrace may take ownership.
 */
export function detectStartupConflicts(
  environment: Readonly<Record<string, string | undefined>>,
): readonly ConflictFinding[] {
  const findings: ConflictFinding[] = [];

  for (const name of Object.keys(environment)) {
    if (CONFLICTING_ENVIRONMENT_PREFIXES.some((prefix) => name.startsWith(prefix))) {
      findings.push({
        code: 'duplicate_provider_or_hook',
        retry: 'never',
        readiness_consequence: 'fail_stream',
        detail: { reason: 'conflicting_otel_environment', variable: name },
      });
    }
  }

  const nodeOptions = environment['NODE_OPTIONS'] ?? '';
  for (const marker of CONFLICTING_PRELOAD_MARKERS) {
    if (nodeOptions.includes(marker)) {
      findings.push({
        code: 'duplicate_provider_or_hook',
        retry: 'never',
        readiness_consequence: 'fail_stream',
        detail: { reason: 'conflicting_preload', marker },
      });
    }
  }

  // eslint-disable-next-line @typescript-eslint/unbound-method -- the wrapper flag is read, never called
  const serverEmit = HttpServer.prototype.emit as unknown as PossiblyWrapped;
  if (serverEmit.__wrapped === true) {
    findings.push(duplicateHook({ reason: 'http_server_emit_already_wrapped' }));
  }

  return findings;
}

/** Turns a failed global registration into a typed duplicate-owner failure. */
export function globalRegistrationConflict(component: string): ConflictFinding {
  return duplicateHook({ reason: 'global_already_registered', component });
}
