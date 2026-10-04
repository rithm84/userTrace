/**
 * Configuration the product surfaces read. Every value is required where it is
 * used: a missing setting raises `ConfigurationError` instead of silently
 * degrading a page to a half-working state.
 *
 * Public values are read through literal `process.env.NEXT_PUBLIC_*` member
 * expressions so the bundler can inline them; server-only values are read from
 * `process.env` at call time and must never be imported into a client module.
 */

export type ConfigurationErrorCode = "missing_setting" | "invalid_setting";

export class ConfigurationError extends Error {
  readonly code: ConfigurationErrorCode;
  readonly setting: string;

  constructor(code: ConfigurationErrorCode, setting: string, message: string) {
    super(message);
    this.name = "ConfigurationError";
    this.code = code;
    this.setting = setting;
  }
}

export type PublicEnvironment = Readonly<{
  supabaseUrl: string;
  supabasePublishableKey: string;
  extensionId: string;
}>;

export type ServerEnvironment = Readonly<{
  apiBaseUrl: string;
  clientId: string;
}>;

function required(setting: string, value: string | undefined): string {
  const trimmed = value?.trim();
  if (trimmed === undefined || trimmed.length === 0) {
    throw new ConfigurationError(
      "missing_setting",
      setting,
      `${setting} is not configured. Set it in the environment before serving uTrace product routes.`,
    );
  }
  return trimmed;
}

function requiredOrigin(setting: string, value: string | undefined): string {
  const raw = required(setting, value);
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new ConfigurationError("invalid_setting", setting, `${setting} must be an absolute URL; received ${raw}.`);
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new ConfigurationError("invalid_setting", setting, `${setting} must use http or https; received ${parsed.protocol}.`);
  }
  return raw.replace(/\/+$/u, "");
}

/** Browser-safe settings. Safe to call from client and server components. */
export function readPublicEnvironment(): PublicEnvironment {
  return Object.freeze({
    supabaseUrl: requiredOrigin("NEXT_PUBLIC_SUPABASE_URL", process.env.NEXT_PUBLIC_SUPABASE_URL),
    supabasePublishableKey: required("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY),
    extensionId: required("NEXT_PUBLIC_UTRACE_EXTENSION_ID", process.env.NEXT_PUBLIC_UTRACE_EXTENSION_ID),
  });
}

/**
 * Server-only settings. `UTRACE_CLIENT_ID` binds the dashboard to the single
 * uTrace Client this deployment serves; staff authorization itself is resolved
 * by the control plane from that client's membership rows.
 */
export function readServerEnvironment(): ServerEnvironment {
  return Object.freeze({
    apiBaseUrl: requiredOrigin("UTRACE_API_BASE_URL", process.env.UTRACE_API_BASE_URL),
    clientId: required("UTRACE_CLIENT_ID", process.env.UTRACE_CLIENT_ID),
  });
}

/** True when the control plane is replaced by the in-repository fixture client. */
export function usesFixtureControlPlane(): boolean {
  return process.env.UTRACE_CONTROL_PLANE_FIXTURES?.trim() === "1";
}

/**
 * True when a participant surface admits the fixture's own participant instead
 * of a Supabase session.
 *
 * Participant sign-in is Discord OAuth, which needs a client secret uTrace does
 * not hold outside production, so a browser-driven test can never reach an
 * authenticated session tab. This exists for exactly that: it is honoured only
 * together with the fixture control plane, it grants no staff access, and it
 * never applies to a deployment serving the real API.
 */
export function usesFixtureParticipant(): boolean {
  return usesFixtureControlPlane() && process.env.UTRACE_PARTICIPANT_FIXTURE?.trim() === "1";
}
