/**
 * The case board: a 1180 x 520 page cut by slanted gutters. The open case fills
 * the left, two thumbnails stack on the right. Every quad is in page coordinates, and the
 * clip and the ink stroke both read from here so they never disagree.
 */

export type Point = readonly [number, number];
export type Quad = readonly [Point, Point, Point, Point];
export type Box = Readonly<{ left: number; top: number; width: number; height: number }>;

export const HELPS_PAGE = Object.freeze({ width: 1180, height: 520, gutter: 12, lean: 36 });
/** Where the gutter between the open case and the thumbnails meets the top edge. */
const SPLIT = 768;
const THUMB_ROWS = 2;

function gutterX(y: number): number {
  return SPLIT - (HELPS_PAGE.lean * y) / HELPS_PAGE.height;
}

/** The open case: the left of the page, its right edge leaning with the gutter. */
export const OPEN_QUAD: Quad = [
  [0, 0],
  [gutterX(0) - HELPS_PAGE.gutter / 2, 0],
  [gutterX(HELPS_PAGE.height) - HELPS_PAGE.gutter / 2, HELPS_PAGE.height],
  [0, HELPS_PAGE.height],
];

/** One of the two waiting cases, top to bottom, each cut by the same leaning gutter. */
export function thumbQuad(slot: number): Quad {
  if (!Number.isInteger(slot) || slot < 0 || slot >= THUMB_ROWS) throw new RangeError(`Thumbnail slot ${slot} is outside 0..${THUMB_ROWS - 1}.`);
  const rowHeight = (HELPS_PAGE.height - HELPS_PAGE.gutter * (THUMB_ROWS - 1)) / THUMB_ROWS;
  const top = slot * (rowHeight + HELPS_PAGE.gutter);
  const bottom = top + rowHeight;
  const inset = HELPS_PAGE.gutter / 2;
  return [
    [gutterX(top) + inset, top],
    [HELPS_PAGE.width, top],
    [HELPS_PAGE.width, bottom],
    [gutterX(bottom) + inset, bottom],
  ];
}

/** The axis-aligned box around a quad, in page coordinates. */
export function quadBox(quad: Quad): Box {
  const xs = quad.map((point) => point[0]);
  const ys = quad.map((point) => point[1]);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  return { left, top, width: Math.max(...xs) - left, height: Math.max(...ys) - top };
}

/** clip-path polygon() in percentages of the quad's own box. */
export function quadClip(quad: Quad): string {
  const box = quadBox(quad);
  return `polygon(${quad.map(([x, y]) => `${(((x - box.left) / box.width) * 100).toFixed(3)}% ${(((y - box.top) / box.height) * 100).toFixed(3)}%`).join(", ")})`;
}

/** SVG points attribute in page coordinates. */
export function quadPoints(quad: Quad): string {
  return quad.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
}
