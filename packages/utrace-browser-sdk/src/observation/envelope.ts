/**
 * Observation envelope assembly.
 *
 * Every observation logically carries the full envelope even though the
 * repeated scope fields are encoded once in the transport batch header.
 * `export_sequence` is a placeholder here: the exporter assigns the real value
 * when it drains the record, so priority ordering and drops cannot leave a hole
 * in the export stream.
 */

import type {
  Observation,
  ObservationEnvelope,
  ObservationPayload,
  ObservationType,
  VisibilityState,
} from "./types.js";

/** Identity of the local runtime producing the observation. */
export interface RuntimeIdentity {
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly frame_id: string;
}

export interface EnvelopeInput {
  readonly observation_id: string;
  readonly observation_type: ObservationType;
  readonly identity: RuntimeIdentity;
  readonly source_sequence: number;
  readonly occurred_at: string;
  readonly monotonic_offset: number;
  readonly route_revision: number;
  readonly visibility_state: VisibilityState;
  readonly target_id: string | null;
  readonly target_revision: number | null;
  readonly trace_id: string | null;
  readonly span_id: string | null;
}

/** Placeholder until the exporter allocates the real export sequence. */
export const UNASSIGNED_EXPORT_SEQUENCE = 0;

export const buildEnvelope = (input: EnvelopeInput): ObservationEnvelope => ({
  observation_id: input.observation_id,
  observation_type: input.observation_type,
  runtime_id: input.identity.runtime_id,
  document_attachment_id: input.identity.document_attachment_id,
  frame_id: input.identity.frame_id,
  source_sequence: input.source_sequence,
  export_sequence: UNASSIGNED_EXPORT_SEQUENCE,
  occurred_at: input.occurred_at,
  monotonic_offset: input.monotonic_offset,
  route_revision: input.route_revision,
  visibility_state: input.visibility_state,
  ...(input.target_id === null ? {} : { target_id: input.target_id }),
  ...(input.target_revision === null ? {} : { target_revision: input.target_revision }),
  ...(input.trace_id === null ? {} : { trace_id: input.trace_id }),
  ...(input.span_id === null ? {} : { span_id: input.span_id }),
});

export const buildObservation = (envelope: ObservationEnvelope, payload: ObservationPayload): Observation => {
  if (envelope.observation_type !== payload.type) {
    throw new Error(
      `uTrace envelope observation_type ${envelope.observation_type} does not match payload type ${payload.type}`,
    );
  }
  return { envelope, payload };
};
