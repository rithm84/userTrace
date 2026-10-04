/**
 * Build-time network configuration.
 *
 * The packaged extension talks to exactly two uTrace control-plane base URLs
 * (the API and the control socket) plus the account-link origin, and accepts
 * externally connectable messages only from the configured uTrace session-tab
 * origins. A base URL may carry a path prefix that every request keeps;
 * possession-proof audiences and the CSP name its origin instead.
 * `config/build-origins.json` is the committed production configuration;
 * `config/build-origins.e2e.json` retargets a build at the local test servers.
 */
import { z } from "zod";

const ORIGIN_PATTERN = /^[a-z][a-z0-9+.-]*:\/\/[^/?#]+$/u;
const BASE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:\/\/[^/?#@]+(\/[^?#]*)?$/u;

export type BuildOrigins = Readonly<{
  /** The control-plane API base URL: an origin and optional path, no trailing slash. */
  apiBaseUrl: string;
  /** The control-socket base URL, under the same rules as `apiBaseUrl`. */
  controlSocketBaseUrl: string;
  accountLinkOrigin: string;
  sessionTabOrigins: readonly string[];
  /**
   * Shipped builds request host access once during onboarding, which Chrome
   * only grants from a user gesture. A test build declares the same hosts as
   * required so an automated run can exercise the surface without that prompt.
   */
  hostPermissions: "optional" | "required";
  /**
   * Base64 SPKI of the RSA public key Chrome derives the extension id from.
   * Pinning it gives every build, packed or unpacked, the same id, which the
   * session tab and the account-link redirect both depend on.
   */
  manifestKey: string;
}>;

export class BuildConfigError extends Error {
  readonly field: string;

  constructor(field: string, message: string) {
    super(message);
    this.name = "BuildConfigError";
    this.field = field;
  }
}

const buildOriginsSchema = z
  .object({
    apiBaseUrl: z.string(),
    controlSocketBaseUrl: z.string(),
    accountLinkOrigin: z.string(),
    sessionTabOrigins: z.array(z.string()).min(1),
    hostPermissions: z.enum(["optional", "required"]),
    manifestKey: z
      .string()
      .min(300)
      .regex(/^[A-Za-z0-9+/]+={0,2}$/, "must be the base64 SPKI of the manifest key"),
  })
  .strict();

/**
 * Validates raw JSON into the build origins.
 *
 * Remote origins must be `https`/`wss`; plaintext is accepted only for
 * loopback development and test builds, which is the single place this
 * package relaxes transport security.
 */
export function parseBuildOrigins(raw: unknown): BuildOrigins {
  const parsed = buildOriginsSchema.safeParse(raw);
  if (!parsed.success) {
    throw new BuildConfigError(
      "build-origins",
      `Build origins are invalid: ${parsed.error.issues
        .map((issue) => `${issue.path.join(".")} ${issue.message}`)
        .join("; ")}.`,
    );
  }

  const value = parsed.data;
  const apiBaseUrl = normaliseBaseUrl("apiBaseUrl", value.apiBaseUrl, ["https:", "http:"]);
  const controlSocketBaseUrl = normaliseBaseUrl(
    "controlSocketBaseUrl",
    value.controlSocketBaseUrl,
    ["wss:", "ws:"],
  );
  assertOrigin("accountLinkOrigin", value.accountLinkOrigin, ["https:", "http:"]);
  for (const origin of value.sessionTabOrigins) {
    assertOrigin("sessionTabOrigins", origin, ["https:", "http:"]);
  }

  return Object.freeze({
    apiBaseUrl,
    controlSocketBaseUrl,
    accountLinkOrigin: value.accountLinkOrigin,
    sessionTabOrigins: Object.freeze([...value.sessionTabOrigins]),
    hostPermissions: value.hostPermissions,
    manifestKey: value.manifestKey,
  });
}

/**
 * Returns the base URL without a trailing slash, so endpoint paths append to
 * it directly. A query, fragment, or credentials would not survive appending a
 * path, so each is refused.
 */
function normaliseBaseUrl(
  field: string,
  baseUrl: string,
  allowedProtocols: readonly string[],
): string {
  if (!BASE_URL_PATTERN.test(baseUrl)) {
    throw new BuildConfigError(
      field,
      `${field} must be an origin with an optional path and no query, fragment, or credentials, such as https://api.utrace.dev/compute/v1/api; received ${JSON.stringify(baseUrl)}.`,
    );
  }

  const url = new URL(baseUrl);
  assertTransport(field, url, allowedProtocols);
  return `${url.origin}${url.pathname.replace(/\/+$/u, "")}`;
}

function assertOrigin(
  field: string,
  origin: string,
  allowedProtocols: readonly string[],
): void {
  if (!ORIGIN_PATTERN.test(origin)) {
    throw new BuildConfigError(
      field,
      `${field} must be a bare origin such as https://api.utrace.dev; received ${JSON.stringify(origin)}.`,
    );
  }

  assertTransport(field, new URL(origin), allowedProtocols);
}

function assertTransport(
  field: string,
  url: URL,
  allowedProtocols: readonly string[],
): void {
  if (!allowedProtocols.includes(url.protocol)) {
    throw new BuildConfigError(
      field,
      `${field} must use one of ${allowedProtocols.join(", ")}; received ${url.protocol}.`,
    );
  }

  const isPlaintext = url.protocol === "http:" || url.protocol === "ws:";
  if (isPlaintext && !isLoopbackHost(url.hostname)) {
    throw new BuildConfigError(
      field,
      `${field} may use plaintext only on a loopback host; received ${url.hostname}.`,
    );
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}
