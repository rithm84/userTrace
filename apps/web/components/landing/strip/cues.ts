import type { PanelId } from "./stripGeometry";

/** The center-panel introduction, typed before the story panels; segments in bold are the names. */
export const CAST_SENTENCES: readonly (readonly { text: string; bold?: boolean }[])[] = [
  [{ text: "An illustrative case." }],
  [{ text: "Shopalytics", bold: true }, { text: " is a fictional analytics product for online stores." }],
  [{ text: "Dudulemon", bold: true }, { text: " is a fictional activewear brand that sells through it." }],
  [{ text: "Maya", bold: true }, { text: " runs growth there." }],
];
/** Typing pace and the pause between sentences. */
export const CAST_TYPING = Object.freeze({ charMs: 26, pauseMs: 500 });
/** When each sentence starts typing. */
export const CAST_STARTS: readonly number[] = CAST_SENTENCES.reduce<number[]>((starts, _sentence, index) => {
  if (index === 0) return [450];
  const previous = CAST_SENTENCES[index - 1].reduce((sum, segment) => sum + segment.text.length, 0);
  return [...starts, starts[index - 1] + previous * CAST_TYPING.charMs + CAST_TYPING.pauseMs];
}, []);

/** Hold the completed introduction for reading, then erase its panel before panel one. */
export const INTRO_EXIT = CAST_STARTS[CAST_SENTENCES.length - 1]
  + CAST_SENTENCES[CAST_SENTENCES.length - 1].reduce((sum, segment) => sum + segment.text.length, 0) * CAST_TYPING.charMs
  + 2662.5;
export const INTRO_END = INTRO_EXIT + 600;

/** Story cues retain their reading pace after the center-panel introduction exits. */
export const CUE = Object.freeze({
  desk: INTRO_END,
  deskChart: INTRO_END + 900,
  ritual: INTRO_END + 6000,
  ritualCursor: INTRO_END + 6300,
  ritualChip: INTRO_END + 6800,
  /** Interval between chips: seven chips land between ritualChip and ritualTally. */
  ritualChipStep: 520,
  ritualTally: INTRO_END + 10800,
  reset: INTRO_END + 12400,
  resetFlash: INTRO_END + 12900,
  resetTumble: INTRO_END + 13200,
  resetBalloon: INTRO_END + 14700,
  request: INTRO_END + 18200,
  requestPost: INTRO_END + 19100,
  requestBurst: INTRO_END + 22400,
  requestRealise: INTRO_END + 23800,
  echo: INTRO_END + 27200,
  echoReddit: INTRO_END + 27500,
  echoAppStore: INTRO_END + 29100,
  echoX: INTRO_END + 30700,
  echoThinking: INTRO_END + 32200,
  reply: INTRO_END + 34200,
  replyTyping: INTRO_END + 34300,
  replyPost: INTRO_END + 35600,
  replyCorroborate: INTRO_END + 37400,
  replyCorroborateStep: 500,
  replySignal: INTRO_END + 39600,
  invite: INTRO_END + 43200,
  inviteMessage: INTRO_END + 43800,
  inviteCard: INTRO_END + 45800,
  inviteButton: INTRO_END + 46600,
  click: INTRO_END + 50200,
  clickCursor: INTRO_END + 51000,
  clickCursorStep: 140,
  /** The cursor presses Join interview: the button becomes Discord's Joined receipt. */
  clickPress: INTRO_END + 53000,
  clickCaption: INTRO_END + 54000,
  end: INTRO_END + 55800,
});

export const STRIP_DURATION = CUE.end;

/** When each panel's border draws and its content becomes visible. */
export const PANEL_CUE: Readonly<Record<PanelId, number>> = Object.freeze({
  desk: CUE.desk,
  ritual: CUE.ritual,
  reset: CUE.reset,
  request: CUE.request,
  echo: CUE.echo,
  reply: CUE.reply,
  invite: CUE.invite,
  click: CUE.click,
});

/** True once the clock has passed a cue. */
export function passed(elapsed: number, cue: number): boolean {
  return elapsed >= cue;
}

/** How many of `count` stepped beats have landed since `start`, spaced `step` apart. */
export function stepsSince(elapsed: number, start: number, step: number, count: number): number {
  if (elapsed < start) return 0;
  return Math.min(count, Math.floor((elapsed - start) / step) + 1);
}

/** How many panels have landed by `elapsed`, out of the page's eight. */
export function panelsLanded(elapsed: number): number {
  return Object.values(PANEL_CUE).filter((cue) => elapsed >= cue).length;
}

/** Panels as a continuous count: the landed panels before the current one, plus how far the current one is through its time. */
export function panelsProgress(elapsed: number): number {
  const landed = panelsLanded(elapsed);
  if (landed === 0) return 0;
  const cues = [...Object.values(PANEL_CUE), STRIP_DURATION].sort((a, b) => a - b);
  const start = cues[landed - 1];
  const end = cues[landed];
  return landed - 1 + Math.min(1, Math.max(0, (elapsed - start) / (end - start)));
}

/**
 * The page's stretches for the scroll, each ending on a frame worth resting on: the typed
 * introduction, then each panel (the first one taking the introduction's exit) until the next
 * one lands, then the finished page.
 */
export const STRIP_SEGMENTS: readonly number[] = [
  0,
  INTRO_EXIT,
  ...Object.values(PANEL_CUE).filter((cue) => cue > CUE.desk).sort((a, b) => a - b),
  STRIP_DURATION,
];
