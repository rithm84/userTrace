import { z } from "zod";

import { DoorConfigurationError } from "./errors";

/**
 * Runtime configuration for the preview front door, validated on every request so a
 * misconfigured deployment fails immediately and visibly instead of routing.
 *
 * `.env.example` documents each variable.
 */

const integerEnv = (params: { min: number; max: number }): z.ZodType<number> =>
  z
    .string()
    .regex(/^\d+$/, "must be a whole number")
    .transform((value) => Number.parseInt(value, 10))
    .refine(
      (value) => value >= params.min && value <= params.max,
      `must be between ${params.min} and ${params.max}`,
    );

const environmentSchema = z.object({
  UTRACE_CONTROL_PLANE_BASE_URL: z.url({ protocol: /^https?$/ }),
  UTRACE_CONTROL_PLANE_SERVICE_TOKEN: z.string().min(16),
  UTRACE_CONTROL_PLANE_TIMEOUT_MS: integerEnv({ min: 100, max: 30_000 }),
  UTRACE_CONTROL_PLANE_ATTEMPTS: integerEnv({ min: 1, max: 5 }),
  UTRACE_CONTROL_PLANE_RETRY_WINDOW_MS: integerEnv({
    min: 0,
    max: 20_000,
  }).optional(),
  UTRACE_PREVIEW_APEX: z
    .string()
    .regex(
      /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/,
      "must be a lowercase host with at least two labels",
    ),
  UTRACE_SESSION_COOKIE_NAME: z
    .string()
    .regex(
      /^__Host-[A-Za-z0-9_-]+$/,
      "must carry the __Host- prefix so the cookie stays bound to one session origin",
    ),
  UTRACE_SESSION_COOKIE_MAX_AGE_SECONDS: integerEnv({ min: 60, max: 86_400 }),
  UTRACE_UPSTREAM_APPLICATION_PORT: integerEnv({ min: 1, max: 65_535 }),
  UTRACE_ROUTE_CONTEXT_TTL_SECONDS: integerEnv({ min: 5, max: 300 }),
});

/**
 * How long a call keeps retrying a control plane that stays unavailable: long enough
 * to ride out a brief ingress interruption, such as the moment a new Preview Version
 * is swapped in, instead of showing the participant an error.
 */
const defaultControlPlaneRetryWindowMs = 8_000;

export interface DoorConfig {
  readonly controlPlaneBaseUrl: string;
  readonly controlPlaneServiceToken: string;
  readonly controlPlaneTimeoutMs: number;
  readonly controlPlaneAttempts: number;
  readonly controlPlaneRetryWindowMs: number;
  readonly previewApex: string;
  readonly sessionCookieName: string;
  readonly sessionCookieMaxAgeSeconds: number;
  readonly upstreamApplicationPort: number;
  readonly routeContextTtlSeconds: number;
}

/** Throws `DoorConfigurationError` when the environment does not satisfy its contract. */
export function readDoorConfig(environment: NodeJS.ProcessEnv): DoorConfig {
  const parsed = environmentSchema.safeParse(environment);
  if (!parsed.success) {
    throw new DoorConfigurationError({
      issues: parsed.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      ),
    });
  }
  const values = parsed.data;
  return {
    controlPlaneBaseUrl: values.UTRACE_CONTROL_PLANE_BASE_URL,
    controlPlaneServiceToken: values.UTRACE_CONTROL_PLANE_SERVICE_TOKEN,
    controlPlaneTimeoutMs: values.UTRACE_CONTROL_PLANE_TIMEOUT_MS,
    controlPlaneAttempts: values.UTRACE_CONTROL_PLANE_ATTEMPTS,
    controlPlaneRetryWindowMs:
      values.UTRACE_CONTROL_PLANE_RETRY_WINDOW_MS ??
      defaultControlPlaneRetryWindowMs,
    previewApex: values.UTRACE_PREVIEW_APEX,
    sessionCookieName: values.UTRACE_SESSION_COOKIE_NAME,
    sessionCookieMaxAgeSeconds: values.UTRACE_SESSION_COOKIE_MAX_AGE_SECONDS,
    upstreamApplicationPort: values.UTRACE_UPSTREAM_APPLICATION_PORT,
    routeContextTtlSeconds: values.UTRACE_ROUTE_CONTEXT_TTL_SECONDS,
  };
}
