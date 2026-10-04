/**
 * The common envelope and the three message families.
 *
 * Internal domain communication uses exactly three families. An Event is an
 * immutable past-tense fact, a Command is one immutable single-purpose request
 * whose `command_id` is its idempotency boundary, and a Result is accepted,
 * progress, or one canonical terminal outcome for a Command.
 *
 * The families are separate schemas rather than one schema with a mode flag,
 * because their required relationships differ: a Command and a Result must carry
 * the Command identity they are bound to, and a Result's payload is the Result
 * body rather than an area payload.
 */

import { z } from "zod";

import { payloadDigest } from "../canonical.js";
import { DigestMismatchError } from "../errors.js";
import type { CanonicalizableValue } from "../json.js";
import { domainIdsSchema, uuidV7Schema } from "../ids/ids.js";
import { actorSchema } from "./actors.js";
import { payloadTypeSchema } from "./compatibility.js";
import { jsonObjectSchema, resultBodySchema } from "./failures.js";
import {
  authorityReferenceSchema,
  causationSchema,
  clientScopeSchema,
  payloadDigestSchema,
  producerSchema,
  streamPositionSchema,
  timestampSchema,
  traceContextSchema,
} from "./values.js";

export const MESSAGE_FAMILIES = ["event", "command", "result"] as const;

export const messageFamilySchema = z.enum(MESSAGE_FAMILIES);

export type MessageFamily = z.infer<typeof messageFamilySchema>;

/**
 * Everything an envelope carries apart from its family and payload.
 *
 * `recorded_at` and `stream.sequence` are assigned by the control plane on
 * acceptance, so a producer omits both. Producer timestamps and Realtime arrival
 * order never decide workflow order; the sequence does.
 */
const envelopeShape = {
  message_id: uuidV7Schema,
  envelope_version: z.int().min(1),
  payload_type: payloadTypeSchema,
  payload_version: z.int().min(1),
  producer: producerSchema,
  actor: actorSchema.optional(),
  client_scope: clientScopeSchema,
  domain_ids: domainIdsSchema,
  authority: z.array(authorityReferenceSchema).max(16).optional(),
  causation: causationSchema.optional(),
  occurred_at: timestampSchema,
  recorded_at: timestampSchema.optional(),
  trace_context: traceContextSchema.optional(),
  stream: streamPositionSchema,
  digest: payloadDigestSchema,
} as const;

/** The common envelope every uTrace domain message carries, in any family. */
export const envelopeSchema = z.strictObject({
  ...envelopeShape,
  family: messageFamilySchema,
  payload: jsonObjectSchema,
});

/** An immutable past-tense fact. Events deduplicate by `message_id`. */
export const eventSchema = z.strictObject({
  ...envelopeShape,
  family: z.literal("event"),
  payload: jsonObjectSchema,
});

const commandShape = z.strictObject({
  ...envelopeShape,
  family: z.literal("command"),
  payload: jsonObjectSchema,
});

/**
 * One immutable, single-purpose request.
 *
 * `domain_ids.command_id` is the idempotency boundary and the provider
 * idempotency key. A repeated Command replays the canonical terminal Result
 * rather than executing again.
 */
export const commandSchema = commandShape.superRefine((message, ctx) => {
  if (message.domain_ids.command_id === undefined) {
    ctx.addIssue({
      code: "custom",
      path: ["domain_ids", "command_id"],
      message:
        "a Command carries domain_ids.command_id: it is the idempotency boundary and the " +
        "provider idempotency key",
    });
  }
});

const resultShape = z.strictObject({
  ...envelopeShape,
  family: z.literal("result"),
  payload: resultBodySchema,
});

/**
 * An authenticated typed answer to one Command.
 *
 * Only an accepted Result advances product state. A provider acknowledgement,
 * Realtime notification, transcript fragment, or model claim never does.
 */
export const resultSchema = resultShape.superRefine((message, ctx) => {
  if (message.domain_ids.command_id === undefined) {
    ctx.addIssue({
      code: "custom",
      path: ["domain_ids", "command_id"],
      message: "a Result carries the domain_ids.command_id it answers",
    });
  }
});

export type Envelope = z.infer<typeof envelopeSchema>;
export type Event = z.infer<typeof eventSchema>;
export type Command = z.infer<typeof commandSchema>;
export type Result = z.infer<typeof resultSchema>;

/**
 * Recompute the payload digest and throw if it disagrees with the envelope.
 *
 * @throws {DigestMismatchError} when the canonical payload does not hash to the
 *   declared value. The message is not trustworthy and must not be applied.
 */
export function verifyDigest(message: {
  readonly message_id: string;
  readonly digest: { readonly value: string };
  readonly payload: CanonicalizableValue;
}): void {
  const computed = payloadDigest(message.payload);
  if (computed !== message.digest.value) {
    throw new DigestMismatchError(message.message_id, message.digest.value, computed);
  }
}
