/**
 * The comic page the hero lifts off: a 1200 x 800 design box scaled to the stage.
 * Three tiers of panels cut by diagonal gutters (treatment B). Every panel is a
 * quadrilateral in page coordinates; the clip and the 2px ink stroke both read
 * from here so they never disagree.
 */

export type Point = readonly [number, number];
export type PanelId = "desk" | "ritual" | "reset" | "request" | "echo" | "reply" | "invite" | "click";

export type PanelShape = Readonly<{
  id: PanelId;
  /** Clockwise from the top-left corner. */
  points: readonly [Point, Point, Point, Point];
}>;

export const PAGE = Object.freeze({ width: 1200, height: 800, gutter: 12, lean: 36 });

/** Tier one holds the drawn desk; tier two needs room for three stacked clippings. */
const TIERS = Object.freeze([
  { top: 0, bottom: 300 },
  { top: 312, bottom: 562 },
  { top: 574, bottom: 800 },
] as const);

function quad(id: PanelId, tier: 0 | 1 | 2, leftTop: number, rightTop: number): PanelShape {
  const { top, bottom } = TIERS[tier];
  const lean = PAGE.lean;
  const x0 = leftTop === 0 ? 0 : leftTop;
  const x0b = leftTop === 0 ? 0 : leftTop - lean;
  const x1 = rightTop === PAGE.width ? PAGE.width : rightTop;
  const x1b = rightTop === PAGE.width ? PAGE.width : rightTop - lean;
  return { id, points: [[x0, top], [x1, top], [x1b, bottom], [x0b, bottom]] };
}

/** Panels in reading order; neighbours in a tier share one 14px slanted gutter. */
export const PANELS: readonly PanelShape[] = Object.freeze([
  quad("desk", 0, 0, 516),
  quad("ritual", 0, 530, 860),
  quad("reset", 0, 874, PAGE.width),
  quad("request", 1, 0, 468),
  quad("echo", 1, 482, 806),
  quad("reply", 1, 820, PAGE.width),
  quad("invite", 2, 0, 506),
  quad("click", 2, 520, PAGE.width),
]);

/** Panels per tier, top to bottom: the page's shape for a miniature. */
export const PANEL_ROWS: readonly number[] = [...new Set(PANELS.map((shape) => shape.points[0][1]))].sort((a, b) => a - b).map((top) => PANELS.filter((shape) => shape.points[0][1] === top).length);

export function panel(id: PanelId): PanelShape {
  const found = PANELS.find((candidate) => candidate.id === id);
  if (found === undefined) throw new Error(`Unknown strip panel ${id}.`);
  return found;
}

/** The axis-aligned box around a panel, in page coordinates. */
export function panelBox(id: PanelId): Readonly<{ left: number; top: number; width: number; height: number }> {
  const xs = panel(id).points.map((p) => p[0]);
  const ys = panel(id).points.map((p) => p[1]);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  return { left, top, width: Math.max(...xs) - left, height: Math.max(...ys) - top };
}

/** clip-path polygon() in percentages of the panel's own box, so the clip survives any scale. */
export function panelClip(id: PanelId): string {
  const box = panelBox(id);
  return `polygon(${panel(id)
    .points.map(([x, y]) => `${(((x - box.left) / box.width) * 100).toFixed(3)}% ${(((y - box.top) / box.height) * 100).toFixed(3)}%`)
    .join(", ")})`;
}

/** SVG points attribute in page coordinates. */
export function panelPoints(id: PanelId): string {
  return panel(id).points.map(([x, y]) => `${x},${y}`).join(" ");
}

/** Perimeter length, for the border draw-on dash. */
export function panelPerimeter(id: PanelId): number {
  const pts = panel(id).points;
  let total = 0;
  for (let i = 0; i < pts.length; i++) {
    const [ax, ay] = pts[i];
    const [bx, by] = pts[(i + 1) % pts.length];
    total += Math.hypot(bx - ax, by - ay);
  }
  return total;
}

/** How the page fits the stage: uniform scale, centred, never above 1.25x. */
export function pageFit(stageWidth: number, stageHeight: number): Readonly<{ scale: number; x: number; y: number }> {
  const scale = Math.min(1.25, (stageWidth - 64) / PAGE.width, (stageHeight - 28) / PAGE.height);
  return { scale, x: (stageWidth - PAGE.width * scale) / 2, y: (stageHeight - PAGE.height * scale) / 2 };
}
