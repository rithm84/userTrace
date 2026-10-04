/**
 * Typed failures raised inside the preview front door.
 *
 * Every message is safe for door logs and error reporting: it names the endpoint, the
 * status, and the typed reason, and never carries a request or response body, a cookie,
 * a handoff code, a session credential, or provider credentials.
 */

/** A required environment variable is missing or does not satisfy its contract. */
export class DoorConfigurationError extends Error {
  readonly issues: readonly string[];

  constructor(params: { issues: readonly string[] }) {
    super(
      `preview front door configuration is invalid: ${params.issues.join("; ")}`,
    );
    this.name = "DoorConfigurationError";
    this.issues = params.issues;
  }
}

/** The control plane answered, but the answer does not satisfy the published contract. */
export class ControlPlaneContractError extends Error {
  readonly endpoint: string;
  readonly status: number;
  readonly issues: readonly string[];

  constructor(params: {
    endpoint: string;
    status: number;
    issues: readonly string[];
  }) {
    super(
      `control plane response does not match its contract: endpoint=${params.endpoint} status=${params.status} issues=${params.issues.join("; ")}`,
    );
    this.name = "ControlPlaneContractError";
    this.endpoint = params.endpoint;
    this.status = params.status;
    this.issues = params.issues;
  }
}

/** The control plane could not be reached, or kept failing, across every permitted attempt. */
export class ControlPlaneUnavailableError extends Error {
  readonly endpoint: string;
  readonly attempts: number;

  constructor(params: { endpoint: string; attempts: number; cause: unknown }) {
    super(
      `control plane is unavailable: endpoint=${params.endpoint} attempts=${params.attempts}`,
      { cause: params.cause },
    );
    this.name = "ControlPlaneUnavailableError";
    this.endpoint = params.endpoint;
    this.attempts = params.attempts;
  }
}

/** The resolved route does not bind the upstream the configuration requires. */
export class UpstreamBindingError extends Error {
  readonly publicPreviewHost: string;
  readonly applicationPort: number;

  constructor(params: {
    publicPreviewHost: string;
    applicationPort: number;
    detail: string;
  }) {
    super(
      `resolved route is not bound to the configured application port: host=${params.publicPreviewHost} port=${params.applicationPort} detail=${params.detail}`,
    );
    this.name = "UpstreamBindingError";
    this.publicPreviewHost = params.publicPreviewHost;
    this.applicationPort = params.applicationPort;
  }
}

/** The runtime hop failed. The door never retries it, because bodies are streamed once. */
export class UpstreamUnavailableError extends Error {
  readonly publicPreviewHost: string;

  constructor(params: { publicPreviewHost: string; cause: unknown }) {
    super(`preview runtime hop failed: host=${params.publicPreviewHost}`, {
      cause: params.cause,
    });
    this.name = "UpstreamUnavailableError";
    this.publicPreviewHost = params.publicPreviewHost;
  }
}
