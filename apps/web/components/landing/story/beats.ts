import type { CharacterState } from "@utrace/agent-character";

import type { AgentColour } from "../characters/AgentSprite";

export type ChapterId = "interview" | "build" | "validate" | "iterate" | "ship";
export type Surface = "session" | "preview" | "workspace" | "casefile";
export type BeatId = "greet" | "filters" | "reset" | "agree" | "gate" | "build" | "ready" | "save" | "partial" | "followup" | "repair" | "validated" | "casefile";

export type Chapter = Readonly<{ id: ChapterId; label: string; colour: AgentColour }>;

/** One chapter, one specialist; the rail shows whichever character a beat puts on screen. */
export const CHAPTERS: readonly Chapter[] = [
  { id: "interview", label: "Interview", colour: "sky" },
  { id: "build", label: "Build", colour: "black" },
  { id: "validate", label: "Validate", colour: "peach" },
  { id: "iterate", label: "Iterate", colour: "lilac" },
  { id: "ship", label: "Ship", colour: "mint" },
];

export type Beat = Readonly<{
  id: BeatId;
  chapter: ChapterId;
  surface: Surface;
  duration: number;
  /** The narration box over the frame's corner. */
  caption: string;
  /**
   * What is said on the call during the beat: the agent's line on the session tab, and Maya's,
   * which answers it there or, in the preview, says what she sees. The transcript keeps both.
   */
  agentLine?: string;
  mayaLine?: string;
  /** The agent's colour and state on this beat's surface. */
  agent: Readonly<{ colour: AgentColour; state: CharacterState }>;
}>;

/** One beat after another at a reading pace (76 seconds of authored time in all); the reader's scroll plays them. */
export const BEATS: readonly Beat[] = [
  { id: "greet", chapter: "interview", surface: "session", duration: 6000, caption: "The interview happens by voice, inside a safe copy of the product.", agentLine: "Hi Maya. Walk me through your Monday.", mayaLine: "Every week I rebuild the same filters. Let me show you.", agent: { colour: "sky", state: "speaking" } },
  { id: "filters", chapter: "interview", surface: "preview", duration: 7500, caption: "The agent watches the product while Maya works, and asks about what it sees.", agent: { colour: "sky", state: "listening" } },
  { id: "reset", chapter: "interview", surface: "preview", duration: 5500, caption: "The agent's evidence records the reset, so nobody has to describe it later.", mayaLine: "Watch: I leave, come back, and they're all gone.", agent: { colour: "sky", state: "concern" } },
  { id: "agree", chapter: "interview", surface: "session", duration: 6000, caption: "They agree on what a fix would have to do.", agentLine: "What if you could save this view by name, and reopening it brought every filter back?", mayaLine: "Exactly. That would give me my Mondays back.", agent: { colour: "sky", state: "speaking" } },
  { id: "gate", chapter: "build", surface: "workspace", duration: 4500, caption: "There is enough evidence to build a bounded change now, during the call.", agent: { colour: "black", state: "idle" } },
  { id: "build", chapter: "build", surface: "workspace", duration: 6500, caption: "The work happens in an isolated workspace that never touches production.", agent: { colour: "black", state: "building" } },
  { id: "ready", chapter: "validate", surface: "session", duration: 4500, caption: "Maya is still on the call when the preview is ready.", agentLine: "The preview is ready. Can you save your Monday view, leave, and reopen it?", agent: { colour: "peach", state: "speaking" } },
  { id: "save", chapter: "validate", surface: "preview", duration: 6500, caption: "Maya tries the exact preview and saves her Monday view.", agent: { colour: "peach", state: "listening" } },
  { id: "partial", chapter: "validate", surface: "preview", duration: 5500, caption: "A partial result means another conversation, not another guess.", mayaLine: "It reopened, but the sizing filter didn't come back.", agent: { colour: "peach", state: "concern" } },
  { id: "followup", chapter: "iterate", surface: "session", duration: 6000, caption: "The agent asks one more question before repairing anything.", agentLine: "When you say sizing, is that the product's size or the topic of the review?", mayaLine: "The review topic. People saying the fit was wrong.", agent: { colour: "lilac", state: "speaking" } },
  { id: "repair", chapter: "iterate", surface: "workspace", duration: 4500, caption: "The repair goes through the same checks as the first build.", agent: { colour: "black", state: "building" } },
  { id: "validated", chapter: "iterate", surface: "preview", duration: 5500, caption: "Only now does uTrace open a pull request.", agent: { colour: "lilac", state: "completion" } },
  { id: "casefile", chapter: "ship", surface: "casefile", duration: 7500, caption: "Everything the team needs to review it arrives together.", agent: { colour: "mint", state: "ship" } },
];

export const BEAT_STARTS: readonly number[] = BEATS.map((_, index) => BEATS.slice(0, index).reduce((sum, beat) => sum + beat.duration, 0));
export const STORY_DURATION = BEATS.reduce((sum, beat) => sum + beat.duration, 0);
/** The story's stretches for the scroll: each beat, resting on its last frame, then the finished story. */
export const STORY_SEGMENTS: readonly number[] = [...BEAT_STARTS, STORY_DURATION];
/** When each chapter's first beat starts, in chapter order. */
export const CHAPTER_STARTS: readonly number[] = CHAPTERS.map((chapter) => {
  const first = BEATS.findIndex((beat) => beat.chapter === chapter.id);
  if (first === -1) throw new Error(`No story beat belongs to ${chapter.id}.`);
  return BEAT_STARTS[first];
});

export function selectBeat(elapsed: number): number {
  for (let index = BEATS.length - 1; index >= 0; index--) if (elapsed >= BEAT_STARTS[index]) return index;
  return 0;
}

/** 0..1 inside the beat at `index`. */
export function beatProgress(elapsed: number, index: number): number {
  return Math.min(1, Math.max(0, (elapsed - BEAT_STARTS[index]) / BEATS[index].duration));
}

/** Maya answers in the second half of a session beat; the agent listens while she does. */
export function mayaTurn(beat: Beat, progress: number): boolean {
  return beat.mayaLine !== undefined && progress >= 0.55;
}

/** The character actually on screen for a beat: the beat's agent, listening once Maya answers what it said. */
export function agentOnScreen(beat: Beat, progress: number): Readonly<{ colour: AgentColour; state: CharacterState }> {
  return beat.agentLine !== undefined && mayaTurn(beat, progress) ? { colour: beat.agent.colour, state: "listening" } : beat.agent;
}

/** 0..1 across every beat of the chapter that owns `elapsed`, so a rail fill never snaps back between beats. */
export function chapterProgress(elapsed: number, id: ChapterId): number {
  const indices = BEATS.map((beat, index) => (beat.chapter === id ? index : -1)).filter((index) => index >= 0);
  if (indices.length === 0) throw new Error(`No story beat belongs to ${id}.`);
  const start = BEAT_STARTS[indices[0]];
  const duration = indices.reduce((sum, index) => sum + BEATS[index].duration, 0);
  return Math.min(1, Math.max(0, (elapsed - start) / duration));
}

/** The last beat of each chapter, for the reduced-motion stills. */
export function lastBeatOf(id: ChapterId): number {
  let last = -1;
  BEATS.forEach((beat, index) => {
    if (beat.chapter === id) last = index;
  });
  if (last === -1) throw new Error(`No story beat belongs to ${id}.`);
  return last;
}
