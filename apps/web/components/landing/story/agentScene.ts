/**
 * The extension's agent cursor and marks, replayed on the demo's clock. The geometry and the
 * timings are the browser extension's own (`apps/browser-extension/src/content/scene.ts`,
 * `sceneGeometry.ts`, `cursor.ts`): the cursor glides to a target on a bowed curve, rides the
 * stroke while it draws a padded loop that overshoots its start, and a mark that is not retained
 * holds and then fades. Here every frame is a pure function of time into the beat, so the demo
 * can loop, pause, and jump to a chapter without replaying anything.
 */

export type Point = Readonly<{ x: number; y: number }>;
/** A registered target's box, top-left based, in the surface's design pixels. */
export type Rect = Readonly<{ x: number; y: number; width: number; height: number }>;
export type Targets = Readonly<Record<string, Rect>>;

const GLIDE_PX_PER_MS = 1.6;
const GLIDE_MIN_MS = 250;
const GLIDE_MAX_MS = 600;
const GLIDE_BOW = 0.12;
/** Where a freshly shown cursor appears, relative to its first destination. */
const ENTRY_OFFSET: Point = { x: -56, y: -40 };
const DRAW_MS = 520;
const MARK_HOLD_MS = 2500;
const MARK_FADE_MS = 400;
const CURSOR_IDLE_MS = 3000;
const MARK_PADDING = 8;
const KAPPA = 0.5522847498;
const SAMPLES_PER_CURVE = 24;

type Cubic = readonly [Point, Point, Point, Point];

export type SceneStep =
  | Readonly<{ kind: "circle"; target: string; at: number }>
  | Readonly<{ kind: "trace"; target: string; at: number }>
  | Readonly<{ kind: "label"; target: string; text: string; at: number }>;

export type SceneMark = Readonly<{ key: string; d: string; drawn: number; opacity: number }>;
export type SceneLabel = Readonly<{ key: string; text: string; target: Rect }>;
export type SceneFrame = Readonly<{ cursor: Point | null; marks: readonly SceneMark[]; labels: readonly SceneLabel[] }>;

export function centreOf(rect: Rect): Point {
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

/** How long the cursor takes to glide between two points: longer for longer distances, within bounds. */
export function glideMs(from: Point, to: Point): number {
  return Math.min(Math.max(Math.hypot(to.x - from.x, to.y - from.y) / GLIDE_PX_PER_MS, GLIDE_MIN_MS), GLIDE_MAX_MS);
}

/** The cursor `linear` (0..1) of the way through a glide: eased in and out along a curve bowed to the left of travel. */
export function glideAt(from: Point, to: Point, linear: number): Point {
  const l = Math.min(1, Math.max(0, linear));
  const t = l < 0.5 ? 4 * l ** 3 : 1 - (-2 * l + 2) ** 3 / 2;
  const u = 1 - t;
  const control = { x: (from.x + to.x) / 2 - (to.y - from.y) * GLIDE_BOW, y: (from.y + to.y) / 2 + (to.x - from.x) * GLIDE_BOW };
  return { x: u * u * from.x + 2 * u * t * control.x + t * t * to.x, y: u * u * from.y + 2 * u * t * control.y + t * t * to.y };
}

/** The loop drawn around a target: an ellipse padded around it in four cubic segments, plus a short overshoot. */
function circleCubics(rect: Rect): readonly Cubic[] {
  const c = centreOf(rect);
  const rx = rect.width / 2 + MARK_PADDING;
  const ry = rect.height / 2 + MARK_PADDING;
  const hx = rx * KAPPA;
  const hy = ry * KAPPA;
  const p = (x: number, y: number): Point => ({ x, y });
  return [
    [p(c.x + rx, c.y), p(c.x + rx, c.y + hy), p(c.x + hx, c.y + ry), p(c.x, c.y + ry)],
    [p(c.x, c.y + ry), p(c.x - hx, c.y + ry), p(c.x - rx, c.y + hy), p(c.x - rx, c.y)],
    [p(c.x - rx, c.y), p(c.x - rx, c.y - hy), p(c.x - hx, c.y - ry), p(c.x, c.y - ry)],
    [p(c.x, c.y - ry), p(c.x + hx, c.y - ry), p(c.x + rx, c.y - hy), p(c.x + rx, c.y)],
    [p(c.x + rx, c.y), p(c.x + rx, c.y + hy * 0.35), p(c.x + rx * 0.72, c.y + ry * 0.55), p(c.x + rx * 0.35, c.y + ry * 0.82)],
  ];
}

/** A single stroke under a target, bowed like a hand line: the extension's quadratic, as one cubic. */
function traceCubics(rect: Rect): readonly Cubic[] {
  const y = rect.y + rect.height + MARK_PADDING;
  const from = { x: rect.x - MARK_PADDING, y };
  const to = { x: rect.x + rect.width + MARK_PADDING, y };
  const bow = { x: (from.x + to.x) / 2, y: y + 4 };
  const lerp = (a: Point, b: Point, k: number): Point => ({ x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k });
  return [[from, lerp(from, bow, 2 / 3), lerp(to, bow, 2 / 3), to]];
}

function cubicAt(curve: Cubic, t: number): Point {
  const u = 1 - t;
  const [a, b, c, d] = curve;
  return { x: u ** 3 * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t ** 3 * d.x, y: u ** 3 * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t ** 3 * d.y };
}

type Stroke = Readonly<{ d: string; points: readonly Point[]; lengths: readonly number[] }>;

/** The stroke's SVG path and an arc-length table, so the cursor rides exactly where the stroke has drawn to. */
function strokeOf(curves: readonly Cubic[]): Stroke {
  const r = (value: number): string => (Math.round(value * 100) / 100).toString();
  const d = `M ${r(curves[0][0].x)} ${r(curves[0][0].y)} ` + curves.map(([, b, c, e]) => `C ${r(b.x)} ${r(b.y)} ${r(c.x)} ${r(c.y)} ${r(e.x)} ${r(e.y)}`).join(" ");
  const points = [curves[0][0], ...curves.flatMap((curve) => Array.from({ length: SAMPLES_PER_CURVE }, (_, i) => cubicAt(curve, (i + 1) / SAMPLES_PER_CURVE)))];
  const lengths = points.reduce<number[]>((acc, point, i) => [...acc, i === 0 ? 0 : acc[i - 1] + Math.hypot(point.x - points[i - 1].x, point.y - points[i - 1].y)], []);
  return { d, points, lengths };
}

function pointAlong(stroke: Stroke, fraction: number): Point {
  const total = stroke.lengths[stroke.lengths.length - 1];
  const want = total * Math.min(1, Math.max(0, fraction));
  const i = Math.max(1, stroke.lengths.findIndex((length) => length >= want));
  const span = stroke.lengths[i] - stroke.lengths[i - 1];
  const k = span === 0 ? 0 : (want - stroke.lengths[i - 1]) / span;
  const a = stroke.points[i - 1];
  const b = stroke.points[i];
  return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k };
}

/**
 * The scene `t` ms into its beat. Each step starts at its `at`, or when the step before it has
 * finished, whichever is later; a step whose target is not registered yet waits. Marks hold and
 * fade like the extension's unretained marks; labels stay for the beat; the cursor fades once
 * nothing has moved it for a while.
 */
export function sceneAt(steps: readonly SceneStep[], targets: Targets, t: number): SceneFrame {
  const marks: SceneMark[] = [];
  const labels: SceneLabel[] = [];
  let cursor: Point | null = null;
  let free = 0;
  let lastMove = Number.NEGATIVE_INFINITY;
  for (const [index, step] of steps.entries()) {
    const rect = targets[step.target];
    const start = Math.max(step.at, free);
    if (rect === undefined || t < start) break;
    const stroke = step.kind === "label" ? null : strokeOf(step.kind === "circle" ? circleCubics(rect) : traceCubics(rect));
    const dest = stroke === null ? centreOf(rect) : stroke.points[0];
    const from = cursor ?? { x: dest.x + ENTRY_OFFSET.x, y: dest.y + ENTRY_OFFSET.y };
    const glide = glideMs(from, dest);
    if (t < start + glide) return { cursor: glideAt(from, dest, (t - start) / glide), marks, labels };
    cursor = dest;
    if (stroke === null) {
      labels.push({ key: `${index}`, text: step.kind === "label" ? step.text : "", target: rect });
      free = lastMove = start + glide;
      continue;
    }
    const drawn = Math.min(1, (t - start - glide) / DRAW_MS);
    const since = t - (start + glide + DRAW_MS);
    marks.push({ key: `${index}`, d: stroke.d, drawn, opacity: since <= MARK_HOLD_MS ? 1 : Math.max(0, 1 - (since - MARK_HOLD_MS) / MARK_FADE_MS) });
    cursor = pointAlong(stroke, drawn);
    if (drawn < 1) return { cursor, marks, labels };
    free = lastMove = start + glide + DRAW_MS;
  }
  return { cursor: cursor !== null && t - lastMove < CURSOR_IDLE_MS ? cursor : null, marks, labels };
}

/** One stop of the customer's own pointer: it sets off for `target` at `at`. */
export type Waypoint = Readonly<{ at: number; target: string }>;

/** Where the customer's pointer is `t` ms into the beat, gliding from `rest` through its waypoints. */
export function pointerAt(waypoints: readonly Waypoint[], targets: Targets, rest: Point, t: number): Point {
  let at = rest;
  for (const waypoint of waypoints) {
    const rect = targets[waypoint.target];
    if (rect === undefined || t < waypoint.at) break;
    const dest = centreOf(rect);
    const glide = glideMs(at, dest);
    if (t < waypoint.at + glide) return glideAt(at, dest, (t - waypoint.at) / glide);
    at = dest;
  }
  return at;
}
