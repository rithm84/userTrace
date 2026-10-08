import { z } from "zod";

/**
 * The live transcript the session tab shows beside the call.
 *
 * The voice agent's Pipecat pipeline reports what each side says as RTVI events
 * over the Daily call: a `user-transcription` for the participant and
 * `bot-output` for the agent, whose `spoken_progress` grows as the agent's speech
 * plays. The tab only displays them. The durable transcript is the backend's own
 * record, and a displayed fragment is neither a completed turn nor evidence (see
 * `docs/arch-components/interview-and-voice.md#transcript-and-evidence-contract`).
 */

export type TranscriptSpeaker = "participant" | "agent";

/** Text to add to the transcript; a leading space separates it from words already shown. */
export type TranscriptDelta = Readonly<{
  speaker: TranscriptSpeaker;
  text: string;
}>;

/** Consecutive fragments from one speaker, joined for reading. */
export type TranscriptTurn = Readonly<{
  key: string;
  speaker: TranscriptSpeaker;
  text: string;
}>;

/** What of each agent output segment the transcript already shows, by RTVI `segment_id`. */
export type ShownSegments = ReadonlyMap<number, string>;

/** A gesture mark the agent writes to point at the screen; it is never spoken. */
const GESTURE_MARK = /<c id="[^"]*"\/>/gu;

const userTranscriptionSchema = z.object({
  text: z.string(),
  final: z.boolean(),
});

const botOutputSchema = z.object({
  text: z.string(),
  segment_id: z.int().nonnegative().optional(),
  spoken_progress: z
    .object({ accumulated_text: z.string(), remaining_text: z.string() })
    .optional(),
});

export class TranscriptEventError extends Error {
  constructor(message: string, options: { cause?: unknown }) {
    super(message, options);
    this.name = "TranscriptEventError";
  }
}

/** The participant's words from one `user-transcription` event; interim transcriptions show nothing. */
export function readUserTranscription(data: unknown): TranscriptDelta | null {
  const event = userTranscriptionSchema.safeParse(data);
  if (!event.success) {
    throw new TranscriptEventError(
      `The call sent a user-transcription event that does not match the RTVI contract: ${event.error.message}`,
      { cause: event.error },
    );
  }
  const text = event.data.text.trim();
  if (!event.data.final || text.length === 0) {
    return null;
  }
  return { speaker: "participant", text: ` ${text}` };
}

/**
 * The agent's newly spoken words from one `bot-output` event.
 *
 * Only `spoken_progress` reaches the transcript, so it shows what the participant
 * heard: text that is not yet (or never) spoken stays out, and an interrupted
 * segment stops where the speech stopped. Gesture marks are removed.
 */
export function readBotOutput(
  shown: ShownSegments,
  data: unknown,
): Readonly<{ shown: ShownSegments; delta: TranscriptDelta | null }> {
  const event = botOutputSchema.safeParse(data);
  if (!event.success) {
    throw new TranscriptEventError(
      `The call sent a bot-output event that does not match the RTVI contract: ${event.error.message}`,
      { cause: event.error },
    );
  }
  const { segment_id: segmentId, spoken_progress: progress } = event.data;
  if (progress === undefined) {
    return { shown, delta: null };
  }
  if (segmentId === undefined) {
    throw new TranscriptEventError("The call sent spoken bot-output progress without a segment_id.", {});
  }

  const spoken = progress.accumulated_text.replace(GESTURE_MARK, "");
  // A segment the transcript has not shown yet has no words on screen.
  const previous = shown.get(segmentId) ?? "";
  if (!spoken.startsWith(previous)) {
    throw new TranscriptEventError(
      `The call's spoken text for segment ${String(segmentId)} no longer starts with the words already shown.`,
      {},
    );
  }
  const added = spoken.slice(previous.length);
  const next = new Map(shown).set(segmentId, spoken);
  if (added.trim().length === 0) {
    return { shown: next, delta: null };
  }
  // A segment's first words start a new sentence after whatever is already shown.
  const text = previous.length === 0 ? ` ${added.trimStart()}` : added;
  return { shown: next, delta: { speaker: "agent", text } };
}

/** The turns after one more fragment: it extends the last turn when the same side is still speaking. */
export function appendTranscriptDelta(
  turns: readonly TranscriptTurn[],
  delta: TranscriptDelta,
): readonly TranscriptTurn[] {
  const last = turns.at(-1);
  if (last !== undefined && last.speaker === delta.speaker) {
    return [...turns.slice(0, -1), { ...last, text: last.text + delta.text }];
  }
  return [
    ...turns,
    { key: `${delta.speaker}-${String(turns.length)}`, speaker: delta.speaker, text: delta.text.trimStart() },
  ];
}
