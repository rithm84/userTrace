import type { CharacterState } from '@utrace/agent-character';

import type { G3 } from './iso';
import type { AgentCue } from './model';

/**
 * The city tells the hero's subhead as one loop: a customer's feedback reaches the chat tower,
 * an agent interviews them at the café, digs under the street for the underlying pain point, the
 * site builds the fix, the trike carries it up the right arm, and the same customer validates it
 * at the shop. The Case File travels the coral street from station to station while each station
 * plays its part, until the site takes it in; what leaves the site is the build it became, a small
 * app window black hands to mint's trike and the customer receives. Everything here is a pure function of the loop's elapsed milliseconds, so the
 * clock can pause and resume anywhere.
 */
const STORY_MS = 20000;

const STATIONS = ['feedback', 'interview', 'pain', 'build', 'validate'] as const;
type Station = (typeof STATIONS)[number];

/** When each station holds the case, in loop milliseconds. */
const WINDOWS: Readonly<Record<Station, readonly [number, number]>> = {
  feedback: [0, 2600],
  interview: [3600, 6600],
  pain: [7400, 9900],
  build: [10700, 13400],
  validate: [16800, 19200],
};

/** The trike waits at the site, rides up the right arm with the case on its cargo box, and parks short of the shop. */
const RIDE = Object.freeze({ start: 13400, end: 15900 });

const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));
const ease = (t: number): number => {
  const x = clamp01(t);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};
const within = (ms: number, [from, to]: readonly [number, number]): number => clamp01((ms - from) / (to - from));
const loop = (ms: number): number => ((ms % STORY_MS) + STORY_MS) % STORY_MS;

function stationAt(ms: number): Station | null {
  const t = loop(ms);
  return STATIONS.find((station) => t >= WINDOWS[station][0] && t < WINDOWS[station][1]) ?? null;
}

/**
 * Where the case waits at each station and how it gets from one to the next: along the coral
 * street (onto the café's interview table and back) to the site, where the Case File goes in; the build then appears at black's side
 * (`HANDOVER`), goes onto the trike's cargo box (two units ahead of the rider, whose route in the
 * plan runs from gy 11 to -24), and hops off the parked trike into the customer's hands.
 */
const STOPS: Readonly<Record<Station, G3>> = {
  feedback: [-26.5, 16, 0.6],
  interview: [-10.7, 20.6, 1.3],
  pain: [-3.4, 15.6, 0.6],
  build: [12.2, 16, 0.6],
  validate: [14.3, -35.4, 1.5],
};
/** Just in front of the frame, at black's right hand. */
const HANDOVER: G3 = [10.2, 13.2, 1.4];
const PICKUP: G3 = [17, 9, 2.5];
const PARKED: G3 = [17, -26, 2.5];
/** `arc` lifts the middle of a leg by that many ground units, so the case hops rather than slides. */
const LEGS: readonly Readonly<{ from: number; to: number; points: readonly G3[]; arc: number }>[] = [
  // the case leaves the street for the café's terrace, landing on the interview table, and back
  { from: 2600, to: 3600, points: [STOPS.feedback, [-12, 16, 0.6], STOPS.interview], arc: 0 },
  { from: 6600, to: 7400, points: [STOPS.interview, [-9, 16, 0.6], STOPS.pain], arc: 0 },
  { from: 9900, to: 10700, points: [STOPS.pain, STOPS.build], arc: 0 },
  // unseen between the Case File going in and the build appearing, the carried thing moves to black's side
  { from: 11300, to: 11400, points: [STOPS.build, HANDOVER], arc: 0 },
  { from: 12800, to: RIDE.start, points: [HANDOVER, [16, 12.6, 1.6], PICKUP], arc: 1 },
  { from: RIDE.start, to: RIDE.end, points: [PICKUP, PARKED], arc: 0 },
  { from: 16050, to: WINDOWS.validate[0], points: [PARKED, STOPS.validate], arc: 3 },
];

function along(points: readonly G3[], t: number): G3 {
  const lengths = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1], p[2] - points[i][2]));
  let rest = t * lengths.reduce((a, b) => a + b, 0);
  for (let i = 0; i < lengths.length; i += 1) {
    if (rest <= lengths[i] || i === lengths.length - 1) {
      const k = lengths[i] === 0 ? 0 : Math.min(1, rest / lengths[i]);
      const [a, b] = [points[i], points[i + 1]];
      return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
    }
    rest -= lengths[i];
  }
  return points[points.length - 1];
}

/**
 * Where the carried thing is, and how much of each form shows: the Case File (`file`) pops in at
 * the tower and fades into the site; the build (`build`) pops in at black's side once the crane
 * has set its panel, and fades at the shop. `rest` is how far the Case File is laid down on the
 * interview table (1 is lying there at table size).
 */
export function caseAt(ms: number): Readonly<{ at: G3; file: number; build: number; rest: number }> {
  const t = loop(ms);
  const rest = ease(within(t, [3250, 3600])) - ease(within(t, [WINDOWS.interview[1], WINDOWS.interview[1] + 350]));
  const file = t < 500 ? t / 500 : 1 - within(t, [10900, 11300]);
  const build = t < 12200 ? 0 : t > 19200 ? clamp01((19700 - t) / 500) : within(t, [12200, 12450]);
  const leg = LEGS.find((entry) => t >= entry.from && t < entry.to);
  if (leg !== undefined) {
    const k = ease(within(t, [leg.from, leg.to]));
    const [x, y, z] = along(leg.points, k);
    return { at: [x, y, z + leg.arc * Math.sin(Math.PI * k)], file, build, rest };
  }
  const previous = [...LEGS].reverse().find((entry) => t >= entry.to);
  return { at: previous === undefined ? STOPS.feedback : previous.points[previous.points.length - 1], file, build, rest };
}

/** The trike: parked at the site, riding with the case (eased exactly like the case's leg), parked short of the shop, then gone until the next loop. */
export function trikeAt(ms: number, distance: number): Readonly<{ travelled: number; opacity: number }> {
  const t = loop(ms);
  const ride = ease(within(t, [RIDE.start, RIDE.end]));
  const opacity = t < 600 ? t / 600 : t > 19200 ? clamp01((19800 - t) / 600) : 1;
  return { travelled: ride * distance, opacity };
}

/** The crane runs its panel out as the case arrives, lowers it in front of the frame's open upper bay, then lifts and returns it clear of the mast. */
export function craneAt(ms: number): Readonly<{ reach: number; lift: number }> {
  const t = loop(ms);
  const out = ease(within(t, [9900, 10900])) - ease(within(t, [14200, 15600]));
  const down = ease(within(t, [10900, 12200])) - ease(within(t, [13200, 14200]));
  // fully out, the panel hangs centred on the open bay (the mast stands at x 3.7, the bay's centre at 11.5)
  return { reach: 2.8 + out * 5, lift: 1 - down };
}

/** How far aqua has climbed out of the dig: 0 is out of sight, 1 is standing in the manhole. */
export function digAt(ms: number): number {
  const t = loop(ms);
  return ease(within(t, [7300, 7900])) - ease(within(t, [9500, 9950]));
}

/** Everything the page renders as state rather than per frame; it changes a few times a loop. */
/** `digShown` is when any of aqua is above the manhole's rim (with a margin), so its rig only renders then. */
export type Scene = Readonly<{ station: Station | null; speaking: boolean; customerTalks: boolean; painShown: boolean; thumbs: boolean; replyShown: boolean; digShown: boolean }>;

export function sceneAt(ms: number): Scene {
  const t = loop(ms);
  return {
    station: stationAt(t),
    speaking: t >= 5100 && t < WINDOWS.interview[1],
    customerTalks: t >= 3700 && t < 5100,
    painShown: t >= 7900 && t < 9550,
    thumbs: t >= 17000 && t < 19100,
    replyShown: t >= 17200 && t < 19200,
    digShown: t >= 7100 && t < 10150,
  };
}

export function sameScene(a: Scene, b: Scene): boolean {
  return a.station === b.station && a.speaking === b.speaking && a.customerTalks === b.customerTalks && a.painShown === b.painShown && a.thumbs === b.thumbs && a.replyShown === b.replyShown && a.digShown === b.digShown;
}

/** The rig state and gaze each agent shows in the current scene. */
export function agentPose(cue: AgentCue, scene: Scene): Readonly<{ state: CharacterState; gazeX: number; gazeY: number }> {
  switch (cue) {
    case 'investigate':
      return { state: 'investigating', gazeX: -0.35, gazeY: -0.7 };
    case 'interview':
      return scene.speaking ? { state: 'speaking', gazeX: -0.85, gazeY: 0 } : { state: 'listening', gazeX: -0.85, gazeY: 0.1 };
    case 'uncover':
      return { state: 'concern', gazeX: 0.2, gazeY: -0.6 };
    case 'build':
      // idle, glancing up at the crane and the frame, until the case reaches the site (a leftward
      // gaze deforms the square body's lower-left corner into a spur)
      return scene.station === 'build' ? { state: 'building', gazeX: 0.5, gazeY: -0.5 } : { state: 'idle', gazeX: 0.3, gazeY: -0.35 };
    case 'validate':
      return scene.station === 'validate' ? { state: 'completion', gazeX: 0.3, gazeY: -0.4 } : { state: 'idle', gazeX: 0.4, gazeY: -0.3 };
    case 'deliver':
      return { state: 'idle', gazeX: 0.8, gazeY: -0.4 };
  }
}
