"use client";

import type { TranscriptDelta, TranscriptSpeaker } from "./transcript";
import type { VoiceSession } from "./voice";

/**
 * The voice call when the control plane is the in-repository fixture.
 *
 * There is no voice agent behind the fixture, so instead of joining a Daily
 * call it replays the Shopalytics discovery conversation as transcript
 * fragments, one word at a time. It exists for
 * local previews and browser tests of the session tab and is loaded only when
 * `UTRACE_CONTROL_PLANE_FIXTURES=1`.
 */

type ScriptedTurn = Readonly<{ speaker: TranscriptSpeaker; text: string }>;

const CONVERSATION: readonly ScriptedTurn[] = [
  {
    speaker: "agent",
    text: "Hi Maya, I'm Ivo. I'm helping the Shopalytics team understand your request about saving conversion views. Could you show me what you do on a Monday?",
  },
  {
    speaker: "participant",
    text: "Sure. I open the conversion dashboard and set Last 30 days, then the TikTok channel, women 25 to 34, and negative reviews about sizing.",
  },
  {
    speaker: "agent",
    text: "I noticed you chose the sizing topic right after the sentiment filter. Is that the order you always use?",
  },
  {
    speaker: "participant",
    text: "Pretty much. Then I pick the product I'm worried about and switch to the conversion metric.",
  },
  {
    speaker: "agent",
    text: "And when you leave the dashboard and come back, what happens to those filters?",
  },
  {
    speaker: "participant",
    text: "They're all gone. I rebuild the whole stack every week, which is why I asked for saved views.",
  },
  {
    speaker: "agent",
    text: "So you'd save this view under a name, reopen it later, and every filter and the conversion chart would come back exactly as you left them. Does that cover it?",
  },
  { speaker: "participant", text: "Yes, that's exactly it." },
];

const WORD_MS = 280;
const TURN_GAP_MS = 900;

export function connectFixtureVoice(
  parameters: Readonly<{ onTranscriptDelta: (delta: TranscriptDelta) => void }>,
): VoiceSession {
  const timers: number[] = [];
  let clock = 0;

  for (const turn of CONVERSATION) {
    clock += TURN_GAP_MS;
    for (const word of turn.text.split(" ")) {
      const delta: TranscriptDelta = { speaker: turn.speaker, text: ` ${word}` };
      timers.push(window.setTimeout(() => parameters.onTranscriptDelta(delta), clock));
      clock += WORD_MS;
    }
  }

  return {
    connectionId: "fixture-voice-connection",
    voiceRunId: "fixture-voice-run",
    setMicrophoneEnabled: (): void => undefined,
    close: (): void => {
      for (const timer of timers) {
        window.clearTimeout(timer);
      }
    },
  };
}
