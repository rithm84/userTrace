/**
 * The typed failure contract and the Result body.
 *
 * Four things stay separate on purpose, because conflating them is how a system
 * starts retrying an effect it already applied or turns an outage into a user
 * rejection: the Command's terminal **disposition**, the **effect state**, which
 * is side-effect certainty, the closed **technical category** describing the
 * cause, and the **retry advice**.
 *
 * The category enumeration is closed. Every member is derived from a failure the
 * architecture already names, and `schemas/envelope/failure.v1.schema.json`
 * documents each one. An error that maps to none of them fails closed as
 * `internal` with retry `never` and creates an engineering-visible finding
 * rather than silently widening the enumeration.
 */

import { z } from "zod";

import type { JsonObject } from "../json.js";
import { snakeCaseNameSchema } from "./values.js";

export const TECHNICAL_CATEGORIES = [
  "authentication",
  "authorization",
  "policy_denied",
  "validation",
  "incompatible_version",
  "not_found",
  "conflict",
  "integrity",
  "timeout",
  "rate_limited",
  "payload_too_large",
  "provider_unavailable",
  "provider_rejected",
  "budget_exhausted",
  "capacity_exhausted",
  "readiness_incomplete",
  "configuration",
  "internal",
] as const;

export const RETRY_ADVICE = ["never", "after_delay", "after_condition_change"] as const;

export const COMMAND_DISPOSITIONS = [
  "succeeded",
  "rejected",
  "failed",
  "canceled",
  "expired",
] as const;

export const EFFECT_STATES = ["absent", "partial", "completed", "indeterminate"] as const;

/** The active providers in the architecture's provider lookup. */
export const PROVIDER_NAMES = [
  "aws",
  "discord",
  "github",
  "openai",
  "opentelemetry",
  "stripe",
  "supabase",
  "typesafe",
  "vercel",
] as const;

export const technicalCategorySchema = z.enum(TECHNICAL_CATEGORIES);
export const retryAdviceSchema = z.enum(RETRY_ADVICE);
export const commandDispositionSchema = z.enum(COMMAND_DISPOSITIONS);
export const effectStateSchema = z.enum(EFFECT_STATES);
export const providerNameSchema = z.enum(PROVIDER_NAMES);

export type TechnicalCategory = z.infer<typeof technicalCategorySchema>;
export type RetryAdvice = z.infer<typeof retryAdviceSchema>;
export type CommandDisposition = z.infer<typeof commandDispositionSchema>;
export type EffectState = z.infer<typeof effectStateSchema>;
export type ProviderName = z.infer<typeof providerNameSchema>;

const jsonObjectSchema: z.ZodType<JsonObject> = z.record(z.string(), z.json());

/** The provider's own error identity, used for reconciliation by stable identity. */
export const providerErrorSchema = z.strictObject({
  provider: providerNameSchema,
  provider_error_code: z.string().min(1).max(128).optional(),
  provider_request_id: z.string().min(1).max(256).optional(),
});

export type ProviderError = z.infer<typeof providerErrorSchema>;

const failureShape = z.strictObject({
  category: technicalCategorySchema,
  code: z.string().regex(/^[a-z][a-z0-9_]*$/).max(96),
  message: z.string().min(1).max(2000),
  retry: retryAdviceSchema,
  retry_after_ms: z.int().min(0).max(86_400_000).optional(),
  retry_condition: z.string().regex(/^[a-z][a-z0-9_]*$/).max(96).optional(),
  provider: providerErrorSchema.optional(),
  details: jsonObjectSchema.optional(),
});

/**
 * One reported failure with redacted, actionable detail.
 *
 * `code` is the stable area-defined reason inside the category, such as
 * `stale_target` or `unsupported_participant_environment`. Codes are additive;
 * the category enumeration is not. Each retry advice carries exactly the field a
 * caller needs to act on it, so advice and payload cannot drift apart.
 */
export const failureSchema = failureShape.superRefine((failure, ctx) => {
  if (failure.retry === "after_delay" && failure.retry_after_ms === undefined) {
    ctx.addIssue({ code: "custom", message: "retry advice after_delay requires retry_after_ms" });
  }
  if (failure.retry !== "after_delay" && failure.retry_after_ms !== undefined) {
    ctx.addIssue({
      code: "custom",
      message: `retry advice ${failure.retry} must not carry retry_after_ms`,
    });
  }
  if (failure.retry === "after_condition_change" && failure.retry_condition === undefined) {
    ctx.addIssue({
      code: "custom",
      message: "retry advice after_condition_change requires retry_condition",
    });
  }
  if (failure.retry !== "after_condition_change" && failure.retry_condition !== undefined) {
    ctx.addIssue({
      code: "custom",
      message: `retry advice ${failure.retry} must not carry retry_condition`,
    });
  }
});

export type Failure = z.infer<typeof failureSchema>;

/** The target admitted the Command. Acceptance proves no external effect. */
export const acceptedResultSchema = z.strictObject({
  status: z.literal("accepted"),
});

/** A non-terminal observation about an in-flight Command. It never settles it. */
export const progressResultSchema = z.strictObject({
  status: z.literal("progress"),
  stage: snakeCaseNameSchema,
  detail: jsonObjectSchema.optional(),
});

const terminalResultShape = z.strictObject({
  status: z.literal("terminal"),
  disposition: commandDispositionSchema,
  effect_state: effectStateSchema,
  failure: failureSchema.optional(),
  output: jsonObjectSchema.optional(),
});

/**
 * The single canonical terminal outcome for a Command.
 *
 * Success carries no failure and a completed effect; a rejection is refused
 * before any side effect, so its effect state is absent.
 */
export const terminalResultSchema = terminalResultShape.superRefine((result, ctx) => {
  if (result.disposition === "succeeded") {
    if (result.failure !== undefined) {
      ctx.addIssue({ code: "custom", message: "a succeeded disposition must not carry a failure" });
    }
    if (result.effect_state !== "completed") {
      ctx.addIssue({
        code: "custom",
        message:
          "a succeeded disposition requires effect_state completed, received " +
          result.effect_state,
      });
    }
  } else if (result.failure === undefined) {
    ctx.addIssue({
      code: "custom",
      message: `disposition ${result.disposition} requires a typed failure`,
    });
  }

  if (result.disposition === "rejected" && result.effect_state !== "absent") {
    ctx.addIssue({
      code: "custom",
      message:
        "a rejected Command is refused before any side effect, so effect_state must be " +
        `absent, received ${result.effect_state}`,
    });
  }
});

export type AcceptedResult = z.infer<typeof acceptedResultSchema>;
export type ProgressResult = z.infer<typeof progressResultSchema>;
export type TerminalResult = z.infer<typeof terminalResultSchema>;

export const resultBodySchema = z.discriminatedUnion("status", [
  acceptedResultSchema,
  progressResultSchema,
  terminalResultSchema,
]);

export type ResultBody = z.infer<typeof resultBodySchema>;

export { jsonObjectSchema };
