/**
 * Public parsers for the envelope area and its binding registry.
 *
 * Each `parse<Name>` validates one untrusted decoded-JSON value into its type,
 * throwing `ZodError` on any violation. The zod schemas enforce the same rules
 * as `schemas/envelope/*.schema.json`; the conformance suite proves the two
 * agree on every fixture, in both languages.
 */

import type { ContractBindings } from "../registry.js";
import { actorSchema, type Actor } from "./actors.js";
import { artifactReferenceSchema, type ArtifactReference } from "./artifacts.js";
import { compatibilityManifestSchema, type CompatibilityManifest } from "./compatibility.js";
import {
  failureSchema,
  resultBodySchema,
  type Failure,
  type ResultBody,
} from "./failures.js";
import {
  commandSchema,
  envelopeSchema,
  eventSchema,
  resultSchema,
  type Command,
  type Envelope,
  type Event,
  type Result,
} from "./messages.js";

/** Validate the common envelope, whatever family it declares. */
export function parseEnvelope(value: unknown): Envelope {
  return envelopeSchema.parse(value);
}

/** Validate an Event: an immutable past-tense fact. */
export function parseEvent(value: unknown): Event {
  return eventSchema.parse(value);
}

/** Validate a Command and its required `domain_ids.command_id`. */
export function parseCommand(value: unknown): Command {
  return commandSchema.parse(value);
}

/** Validate a Result, including the coherence of its terminal outcome. */
export function parseResult(value: unknown): Result {
  return resultSchema.parse(value);
}

/** Validate a Result body on its own: accepted, progress, or one terminal outcome. */
export function parseResultBody(value: unknown): ResultBody {
  return resultBodySchema.parse(value);
}

/** Validate an actor of any kind. */
export function parseActor(value: unknown): Actor {
  return actorSchema.parse(value);
}

/** Validate a typed failure and its retry advice. */
export function parseFailure(value: unknown): Failure {
  return failureSchema.parse(value);
}

/** Validate an immutable artifact reference. */
export function parseArtifactReference(value: unknown): ArtifactReference {
  return artifactReferenceSchema.parse(value);
}

/** Validate a component's advertised compatibility manifest. */
export function parseCompatibilityManifest(value: unknown): CompatibilityManifest {
  return compatibilityManifestSchema.parse(value);
}

export const areaContracts: ContractBindings = {
  "envelope.v1": parseEnvelope,
  "event.v1": parseEvent,
  "command.v1": parseCommand,
  "result.v1": parseResult,
  "result-body.v1": parseResultBody,
  "actor.v1": parseActor,
  "failure.v1": parseFailure,
  "artifact-reference.v1": parseArtifactReference,
  "compatibility-manifest.v1": parseCompatibilityManifest,
};
