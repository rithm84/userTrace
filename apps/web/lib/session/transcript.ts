import { z } from "zod";

/**
 * The live transcript the session tab shows beside the call.
 *
 * GPT-Live streams what each side says as fragments on the WebRTC data channel:
 * `session.input_transcript.delta` for the participant and
 * `session.output_transcript.delta` for the agent, each a text fragment with its
 * interval on the session timeline. The tab only displays them. The durable
 * transcript is the backend's copy from the sideband, and a displayed fragment
 * is neither a completed turn nor evidence (see
 * `docs/arch-components/interview-and-voice.md#transcript-and-evidence-contract`).
 */

export type TranscriptSpeaker = "participant" | "agent";

export type TranscriptDelta = Readonly<{
  speaker: TranscriptSpeaker;
  text: string;
  startMs: number;
  endMs: number;
}>;

/** Consecutive fragments from one speaker, joined for reading. */
export type TranscriptTurn = Readonly<{
  key: string;
  speaker: TranscriptSpeaker;
  text: string;
  startMs: number;
  endMs: number;
}>;

const INPUT_DELTA = "session.input_transcript.delta";
const OUTPUT_DELTA = "session.output_transcript.delta";

const deltaEventSchema = z.object({
  type: z.enum([INPUT_DELTA, OUTPUT_DELTA]),
  delta: z.string(),
  start_ms: z.number().nonnegative(),
  end_ms: z.number().nonnegative(),
});

const eventTypeSchema = z.object({ type: z.string().min(1) });

export class TranscriptEventError extends Error {
  constructor(message: string, options: { cause?: unknown }) {
    super(message, options);
    this.name = "TranscriptEventError";
  }
}

/**
 * One data-channel message, read as a transcript fragment.
 *
 * Returns `null` for the channel's other event types, which the tab does not
 * display. A message that is not JSON, or a transcript event whose fields do not
 * match the contract, raises.
 */
export function readTranscriptMessage(message: string): TranscriptDelta | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(message);
  } catch (error) {
    throw new TranscriptEventError("The voice data channel sent a message that is not JSON.", { cause: error });
  }

  const typed = eventTypeSchema.safeParse(parsed);
  if (!typed.success) {
    throw new TranscriptEventError("The voice data channel sent an event without a type.", { cause: typed.error });
  }
  if (typed.data.type !== INPUT_DELTA && typed.data.type !== OUTPUT_DELTA) {
    return null;
  }

  const event = deltaEventSchema.safeParse(parsed);
  if (!event.success) {
    throw new TranscriptEventError(
      `The voice data channel sent a ${typed.data.type} event that does not match the transcript contract: ${event.error.message}`,
      { cause: event.error },
    );
  }
  return {
    speaker: event.data.type === INPUT_DELTA ? "participant" : "agent",
    text: event.data.delta,
    startMs: event.data.start_ms,
    endMs: event.data.end_ms,
  };
}

/** The turns after one more fragment: it extends the last turn when the same side is still speaking. */
export function appendTranscriptDelta(
  turns: readonly TranscriptTurn[],
  delta: TranscriptDelta,
): readonly TranscriptTurn[] {
  const last = turns.at(-1);
  if (last !== undefined && last.speaker === delta.speaker) {
    return [
      ...turns.slice(0, -1),
      { ...last, text: last.text + delta.text, endMs: Math.max(last.endMs, delta.endMs) },
    ];
  }
  return [
    ...turns,
    {
      key: `${delta.speaker}-${String(delta.startMs)}-${String(turns.length)}`,
      speaker: delta.speaker,
      text: delta.text.trimStart(),
      startMs: delta.startMs,
      endMs: delta.endMs,
    },
  ];
}
