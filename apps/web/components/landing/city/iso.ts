/**
 * Isometric projection for the hero's city. Ground coordinates (gx, gy, gz) are grid units:
 * +gx runs down-right on screen, +gy down-left, +gz up. Everything is projected to design
 * pixels here and drawn as plain screen-space paths, so strokes keep one weight on every face.
 */
export type Pt = readonly [number, number];
export type G3 = readonly [number, number, number];

/** One grid unit in design pixels, and the true-isometric 30 degree axes. */
const UNIT = 20;
const AX = Math.cos(Math.PI / 6) * UNIT;
const AY = 0.5 * UNIT;
/** Where ground (0, 0, 0) lands in the 2400 x 1000 design box. */
const ORIGIN = Object.freeze({ x: 1200, y: 640 });

export function iso(g: G3): Pt {
  return [ORIGIN.x + (g[0] - g[1]) * AX, ORIGIN.y + (g[0] + g[1]) * AY - g[2] * UNIT];
}

/** Screen-space displacement of a ground-space step, for moving actors. */
export function isoDelta(dx: number, dy: number, dz: number): Pt {
  return [(dx - dy) * AX, (dx + dy) * AY - dz * UNIT];
}

const n = (value: number): string => (Math.round(value * 10) / 10).toString();

export function closedPath(points: readonly Pt[]): string {
  if (points.length < 3) throw new RangeError(`A closed path needs three points; got ${points.length}.`);
  return 'M' + points.map((p) => `${n(p[0])} ${n(p[1])}`).join('L') + 'Z';
}

export function openPath(points: readonly Pt[]): string {
  if (points.length < 2) throw new RangeError(`An open path needs two points; got ${points.length}.`);
  return 'M' + points.map((p) => `${n(p[0])} ${n(p[1])}`).join('L');
}

/** An axis-aligned box in ground units: origin corner (x, y, z) and extents (w along gx, d along gy, h up). */
export type Box = Readonly<{ x: number; y: number; z: number; w: number; d: number; h: number }>;

/**
 * A flat face with its own 2D frame: `at(u, v)` maps face units (u across, v up or along) to the
 * screen, and `off(u, v, n)` the same point pushed n units out along the face's outward normal.
 */
export type Face = Readonly<{ at: (u: number, v: number) => Pt; off: (u: number, v: number, n: number) => Pt; width: number; height: number }>;

/** The +gy face, seen on the lower left of a box; u runs toward +gx (left to right on screen). */
export function leftFace(b: Box): Face {
  const off = (u: number, v: number, n: number): Pt => iso([b.x + u, b.y + b.d + n, b.z + v]);
  return { at: (u, v) => off(u, v, 0), off, width: b.w, height: b.h };
}

/** The +gx face, seen on the lower right of a box; u runs toward -gy (left to right on screen). */
export function rightFace(b: Box): Face {
  const off = (u: number, v: number, n: number): Pt => iso([b.x + b.w + n, b.y + b.d - u, b.z + v]);
  return { at: (u, v) => off(u, v, 0), off, width: b.d, height: b.h };
}

/** The top face; u runs along +gx, v along +gy. */
export function topFace(b: Box): Face {
  const off = (u: number, v: number, n: number): Pt => iso([b.x + u, b.y + v, b.z + b.h + n]);
  return { at: (u, v) => off(u, v, 0), off, width: b.w, height: b.d };
}

/** The screen tile of a ground grid of `k`-unit squares at height z: a rhombus that repeats every 2k·AX by 2k·AY, anchored on ground (0, 0). */
export function latticeTile(k: number, z: number): Readonly<{ x: number; y: number; width: number; height: number }> {
  return { x: ORIGIN.x - AX * k, y: ORIGIN.y - z * UNIT, width: 2 * AX * k, height: 2 * AY * k };
}

/** A horizontal rectangle at height z, in ground units. */
export function groundRect(x: number, y: number, w: number, d: number, z: number): string {
  return closedPath([iso([x, y, z]), iso([x + w, y, z]), iso([x + w, y + d, z]), iso([x, y + d, z])]);
}

export function faceRect(f: Face, u: number, v: number, w: number, h: number): string {
  return closedPath([f.at(u, v), f.at(u + w, v), f.at(u + w, v + h), f.at(u, v + h)]);
}

const ARC_STEPS = 6;

/** A rounded rectangle on a face, sampled so it stays a true screen-space path with even strokes. */
export function faceRoundRect(f: Face, u: number, v: number, w: number, h: number, r: number): string {
  const radius = Math.min(r, w / 2, h / 2);
  const corners: readonly (readonly [number, number, number])[] = [
    [u + w - radius, v + radius, -Math.PI / 2],
    [u + w - radius, v + h - radius, 0],
    [u + radius, v + h - radius, Math.PI / 2],
    [u + radius, v + radius, Math.PI],
  ];
  const points: Pt[] = [];
  for (const [cu, cv, start] of corners) {
    for (let i = 0; i <= ARC_STEPS; i += 1) {
      const angle = start + (i / ARC_STEPS) * (Math.PI / 2);
      points.push(f.at(cu + Math.cos(angle) * radius, cv + Math.sin(angle) * radius));
    }
  }
  return closedPath(points);
}

/** An ellipse on a face (a circle when rx = ry), sampled in face space. */
export function faceEllipse(f: Face, cu: number, cv: number, rx: number, ry: number): string {
  const points: Pt[] = [];
  const steps = 28;
  for (let i = 0; i < steps; i += 1) {
    const angle = (i / steps) * Math.PI * 2;
    points.push(f.at(cu + Math.cos(angle) * rx, cv + Math.sin(angle) * ry));
  }
  return closedPath(points);
}

export function faceLine(f: Face, points: readonly (readonly [number, number])[]): string {
  return openPath(points.map(([u, v]) => f.at(u, v)));
}

/** Screen bounds of a box's eight corners. */
export function screenBounds(b: Box): Readonly<{ left: number; right: number; top: number; bottom: number }> {
  const xs: number[] = [];
  const ys: number[] = [];
  for (const dx of [0, b.w]) for (const dy of [0, b.d]) for (const dz of [0, b.h]) {
    const [sx, sy] = iso([b.x + dx, b.y + dy, b.z + dz]);
    xs.push(sx);
    ys.push(sy);
  }
  return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
}
