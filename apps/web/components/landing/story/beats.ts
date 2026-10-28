import type { CharacterState } from "@utrace/agent-character";

import type { AgentColour } from "../characters/AgentSprite";

export type ChapterId = "request" | "interview" | "build" | "validate" | "iterate" | "ship";
export type Surface = "discord" | "session" | "preview" | "workspace" | "casefile";
export type BeatId = "request" | "signal" | "invite" | "greet" | "filters" | "reset" | "agree" | "build" | "ready" | "save" | "partial" | "followup" | "repair" | "validated" | "thanks" | "casefile";

export type Chapter = Readonly<{ id: ChapterId; label: string; colour: AgentColour }>;

/**
 * The one agent the product shows Maya, in every tab: the uTrace app's voice in Discord, the
 * session tab's character and the extension's orb. The rail's cast stays one specialist per chapter.
 */
export const PRODUCT_AGENT = Object.freeze({ name: "Wren", colour: "coral" as AgentColour });

/** One chapter, one specialist; the rail shows the beat's specialist. */
export const CHAPTERS: readonly Chapter[] = [
  { id: "request", label: "Request", colour: "coral" },
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
  /** The rail's specialist for the beat, and the state the product's agent shares with it. */
  agent: Readonly<{ colour: AgentColour; state: CharacterState }>;
}>;

/**
 * One beat after another, each as long as it takes to read: a beat with more to read holds
 * longer, a simple one moves on sooner. About 67 seconds of authored time in all, which the demo's
 * clock plays at `PLAYBACK_RATE` and loops.
 */
export const BEATS: readonly Beat[] = [
  { id: "request", chapter: "request", surface: "discord", duration: 4000, caption: "It starts with Maya's request in Shopalytics' Discord.", agent: { colour: "coral", state: "investigating" } },
  { id: "signal", chapter: "request", surface: "discord", duration: 4500, caption: "uTrace replies in public, then follows up with Maya in a DM.", agent: { colour: "coral", state: "thinking" } },
  { id: "invite", chapter: "request", surface: "discord", duration: 5000, caption: "Only Maya gets the invitation, and her team gets a credit for her time.", agent: { colour: "coral", state: "speaking" } },
  { id: "greet", chapter: "interview", surface: "session", duration: 4000, caption: "The interview happens by voice, inside a safe copy of the product.", agentLine: "Hi Maya, it's Wren. Walk me through your Monday.", mayaLine: "Every week I rebuild the same filters. Let me show you.", agent: { colour: "sky", state: "speaking" } },
  { id: "filters", chapter: "interview", surface: "preview", duration: 5500, caption: "The agent watches the product while Maya works, and circles what it asks about.", agent: { colour: "sky", state: "listening" } },
  { id: "reset", chapter: "interview", surface: "preview", duration: 3500, caption: "The agent's evidence records the reset, so nobody has to describe it later.", mayaLine: "Watch: I leave, come back, and they're all gone.", agent: { colour: "sky", state: "concern" } },
  { id: "agree", chapter: "interview", surface: "session", duration: 4500, caption: "They agree on what a fix would have to do.", agentLine: "What if you could save this view by name, and reopening it brought every filter back?", mayaLine: "Exactly. That would give me my Mondays back.", agent: { colour: "sky", state: "speaking" } },
  { id: "build", chapter: "build", surface: "workspace", duration: 4500, caption: "The change is built during the call, in a workspace that never touches production.", agent: { colour: "black", state: "building" } },
  { id: "ready", chapter: "validate", surface: "session", duration: 2700, caption: "Maya is still on the call when the preview is ready.", agentLine: "The preview is ready. Can you save your Monday view, leave, and reopen it?", agent: { colour: "peach", state: "speaking" } },
  { id: "save", chapter: "validate", surface: "preview", duration: 4500, caption: "Maya tries the exact preview and saves her Monday view.", agent: { colour: "peach", state: "listening" } },
  { id: "partial", chapter: "validate", surface: "preview", duration: 4300, caption: "A partial result means another conversation, not another guess.", mayaLine: "It reopened, but the sizing filter didn't come back.", agent: { colour: "peach", state: "concern" } },
  { id: "followup", chapter: "iterate", surface: "session", duration: 4500, caption: "The agent asks one more question before repairing anything.", agentLine: "When you say sizing, is that the product's size or the topic of the review?", mayaLine: "The review topic. People saying the fit was wrong.", agent: { colour: "lilac", state: "speaking" } },
  { id: "repair", chapter: "iterate", surface: "workspace", duration: 2600, caption: "The repair goes through the same checks as the first build.", agent: { colour: "black", state: "building" } },
  { id: "validated", chapter: "iterate", surface: "preview", duration: 3500, caption: "Only now does uTrace open a pull request.", agent: { colour: "lilac", state: "completion" } },
  { id: "thanks", chapter: "ship", surface: "discord", duration: 3800, caption: "Maya's time earns Dudulemon money off its next bill.", agent: { colour: "mint", state: "completion" } },
  { id: "casefile", chapter: "ship", surface: "casefile", duration: 5500, caption: "Everything the team needs to review it arrives together.", agent: { colour: "mint", state: "ship" } },
];

export const BEAT_STARTS: readonly number[] = BEATS.map((_, index) => BEATS.slice(0, index).reduce((sum, beat) => sum + beat.duration, 0));
export const STORY_DURATION = BEATS.reduce((sum, beat) => sum + beat.duration, 0);
/** The demo plays its authored time a little slower than real time: about 74 seconds in all. */
export const PLAYBACK_RATE = 0.9;
/** How long the finished Case File holds before the demo starts over. */
export const LOOP_HOLD_MS = 4000;
/** Maya joins the interview here: the session and preview tabs open beside Discord from this beat on. */
export const JOIN_INDEX = BEATS.findIndex((beat) => beat.id === "greet");
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
