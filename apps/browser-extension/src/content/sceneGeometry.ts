/**
 * Scene geometry.
 *
 * Every mark is derived from the current structured target geometry the shared
 * browser observation runtime supplies. The model never supplies freehand
 * coordinates, and nothing here falls back to a guessed element or a nearest
 * point: an unusable target is a typed refusal the caller reports.
 */
import type { ResolvedTarget } from "../protocol/bridge";
import type { ViewportRect } from "../protocol/common";
import type { Point, Viewport } from "./orbGeometry";

/** A target must be at least this visible before a gesture may point at it. */
export const MINIMUM_VISIBLE_FRACTION = 0.6;

export type TargetRefusal = Readonly<{
  reason: "target_obscured" | "target_offscreen" | "target_not_visual" | "target_degenerate";
}>;

export type TargetCheck = Readonly<{ usable: true }> | (Readonly<{ usable: false }> & TargetRefusal);

/**
 * The check run immediately before a gesture executes. Occlusion and clipped
 * geometry are facts the SDK reports for the real element; the extension's own
 * surface is excluded from those checks by construction, because it is not a
 * registered product target.
 */
export function checkTarget(target: ResolvedTarget, viewport: Viewport): TargetCheck {
  if (!target.supports_visual_guidance) {
    return { usable: false, reason: "target_not_visual" };
  }
  if (target.rect.width <= 0 || target.rect.height <= 0) {
    return { usable: false, reason: "target_degenerate" };
  }
  if (!target.in_viewport || !intersectsViewport(target.rect, viewport)) {
    return { usable: false, reason: "target_offscreen" };
  }
  if (target.occluded || target.visible_fraction < MINIMUM_VISIBLE_FRACTION) {
    return { usable: false, reason: "target_obscured" };
  }
  return { usable: true };
}

export function centreOf(rect: ViewportRect): Point {
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

/**
 * The comic loop the cursor draws around a target: an ellipse padded around
 * the rect, built from four cubic segments with a small overshoot at the end
 * so the stroke crosses itself the way a drawn circle does.
 */
export function circlePath(rect: ViewportRect, padding: number): string {
  const centre = centreOf(rect);
  const radiusX = rect.width / 2 + padding;
  const radiusY = rect.height / 2 + padding;
  const kappa = 0.5522847498;
  const handleX = radiusX * kappa;
  const handleY = radiusY * kappa;
  const start = { x: centre.x + radiusX, y: centre.y };

  return [
    `M ${round(start.x)} ${round(start.y)}`,
    `C ${round(centre.x + radiusX)} ${round(centre.y + handleY)} ${round(centre.x + handleX)} ${round(centre.y + radiusY)} ${round(centre.x)} ${round(centre.y + radiusY)}`,
    `C ${round(centre.x - handleX)} ${round(centre.y + radiusY)} ${round(centre.x - radiusX)} ${round(centre.y + handleY)} ${round(centre.x - radiusX)} ${round(centre.y)}`,
    `C ${round(centre.x - radiusX)} ${round(centre.y - handleY)} ${round(centre.x - handleX)} ${round(centre.y - radiusY)} ${round(centre.x)} ${round(centre.y - radiusY)}`,
    `C ${round(centre.x + handleX)} ${round(centre.y - radiusY)} ${round(centre.x + radiusX)} ${round(centre.y - handleY)} ${round(centre.x + radiusX)} ${round(centre.y)}`,
    `C ${round(centre.x + radiusX)} ${round(centre.y + handleY * 0.35)} ${round(centre.x + radiusX * 0.72)} ${round(centre.y + radiusY * 0.55)} ${round(centre.x + radiusX * 0.35)} ${round(centre.y + radiusY * 0.82)}`,
  ].join(" ");
}

export type TraceEdge = "top" | "bottom" | "leading" | "trailing";

/** A single drawn stroke along one edge of a target, bowed like a hand line. */
export function tracePath(rect: ViewportRect, edge: TraceEdge, padding: number): string {
  const { from, to, bow } = traceEnds(rect, edge, padding);
  const midpoint = { x: (from.x + to.x) / 2 + bow.x, y: (from.y + to.y) / 2 + bow.y };
  return `M ${round(from.x)} ${round(from.y)} Q ${round(midpoint.x)} ${round(midpoint.y)} ${round(to.x)} ${round(to.y)}`;
}

function traceEnds(
  rect: ViewportRect,
  edge: TraceEdge,
  padding: number,
): Readonly<{ from: Point; to: Point; bow: Point }> {
  const left = rect.x - padding;
  const right = rect.x + rect.width + padding;
  const top = rect.y - padding;
  const bottom = rect.y + rect.height + padding;
  const bowSize = 4;

  switch (edge) {
    case "top":
      return { from: { x: left, y: top }, to: { x: right, y: top }, bow: { x: 0, y: -bowSize } };
    case "bottom":
      return {
        from: { x: left, y: bottom },
        to: { x: right, y: bottom },
        bow: { x: 0, y: bowSize },
      };
    case "leading":
      return { from: { x: left, y: top }, to: { x: left, y: bottom }, bow: { x: -bowSize, y: 0 } };
    case "trailing":
      return {
        from: { x: right, y: top },
        to: { x: right, y: bottom },
        bow: { x: bowSize, y: 0 },
      };
  }
}

export type LabelPlacement = "above" | "below" | "leading" | "trailing";

export type LabelBox = Readonly<{ x: number; y: number; width: number; height: number }>;

/**
 * Places a label beside its target and keeps it inside the viewport. The
 * placement the command asked for is honoured when it fits and flipped to the
 * opposite side when it does not, so a label never leaves the screen.
 */
export function labelBox(
  rect: ViewportRect,
  placement: LabelPlacement,
  size: Readonly<{ width: number; height: number }>,
  viewport: Viewport,
  gap: number,
): LabelBox {
  const centre = centreOf(rect);
  const candidates: Record<LabelPlacement, LabelBox> = {
    above: { x: centre.x - size.width / 2, y: rect.y - gap - size.height, ...size },
    below: { x: centre.x - size.width / 2, y: rect.y + rect.height + gap, ...size },
    leading: { x: rect.x - gap - size.width, y: centre.y - size.height / 2, ...size },
    trailing: { x: rect.x + rect.width + gap, y: centre.y - size.height / 2, ...size },
  };
  const opposite: Record<LabelPlacement, LabelPlacement> = {
    above: "below",
    below: "above",
    leading: "trailing",
    trailing: "leading",
  };

  const preferred = candidates[placement];
  const chosen = fitsViewport(preferred, viewport) ? preferred : candidates[opposite[placement]];
  return {
    x: clamp(chosen.x, gap, Math.max(gap, viewport.width - size.width - gap)),
    y: clamp(chosen.y, gap, Math.max(gap, viewport.height - size.height - gap)),
    width: size.width,
    height: size.height,
  };
}

function fitsViewport(box: LabelBox, viewport: Viewport): boolean {
  return (
    box.x >= 0 &&
    box.y >= 0 &&
    box.x + box.width <= viewport.width &&
    box.y + box.height <= viewport.height
  );
}

function intersectsViewport(rect: ViewportRect, viewport: Viewport): boolean {
  return (
    rect.x + rect.width > 0 &&
    rect.y + rect.height > 0 &&
    rect.x < viewport.width &&
    rect.y < viewport.height
  );
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
