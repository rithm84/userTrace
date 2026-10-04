/**
 * Static browser configuration.
 *
 * The contract allows exactly five fields: a public SDK installation id, the
 * client-defined environment name, the client release id, and the supported
 * schema and protocol versions. It carries no secret, no uTrace User identity,
 * no customer account identity, no session identity, no ingestion credential,
 * and no executable policy. Every other identity arrives inside the signed
 * document attestation.
 */

import { ConfigurationError } from "./failure.js";
import { SUPPORTED_PROTOCOL_VERSION, SUPPORTED_SCHEMA_VERSION } from "./versions.js";

export interface BrowserSdkConfig {
  readonly sdkInstallationId: string;
  readonly environmentName: string;
  readonly clientReleaseId: string;
  readonly schemaVersion: string;
  readonly protocolVersion: string;
}

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

const requireOpaque = (field: string, value: string): string => {
  if (!OPAQUE_ID.test(value)) {
    throw new ConfigurationError(field, "must be 1-128 characters of [A-Za-z0-9._-] starting alphanumeric");
  }
  return value;
};

/**
 * Validates static configuration and rejects a build that speaks a schema or
 * protocol major this SDK does not implement. There is no lower-fidelity
 * substitution for an incompatible version.
 */
export const validateConfig = (input: BrowserSdkConfig): BrowserSdkConfig => {
  if (input.schemaVersion !== SUPPORTED_SCHEMA_VERSION) {
    throw new ConfigurationError(
      "schemaVersion",
      `this build implements schema version ${SUPPORTED_SCHEMA_VERSION}, received ${input.schemaVersion}`,
    );
  }
  if (input.protocolVersion !== SUPPORTED_PROTOCOL_VERSION) {
    throw new ConfigurationError(
      "protocolVersion",
      `this build implements protocol version ${SUPPORTED_PROTOCOL_VERSION}, received ${input.protocolVersion}`,
    );
  }
  return {
    sdkInstallationId: requireOpaque("sdkInstallationId", input.sdkInstallationId),
    environmentName: requireOpaque("environmentName", input.environmentName),
    clientReleaseId: requireOpaque("clientReleaseId", input.clientReleaseId),
    schemaVersion: input.schemaVersion,
    protocolVersion: input.protocolVersion,
  };
};
