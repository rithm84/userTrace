/**
 * Where the orb, its control pills, and the End confirmation sit.
 *
 * The orb's position and size are device-local and shared across clients, and
 * it is clamped far enough inside the viewport that the four expanded control
 * pills stay visible. Layout changes, scrolling, and SPA transitions do not
 * move it.
 *
 * Every length this module returns is a whole CSS pixel: a canvas or a label
 * placed at a fractional offset is resampled by the compositor and renders
 * soft, so the orb's centre is snapped and every offset is built from even
 * sizes.
 */
import type { OrbSettings } from "../shared/portMessages";

export type Viewport = Readonly<{ width: number; height: number }>;
export type Point = Readonly<{ x: number; y: number }>;
export type Size = Readonly<{ width: number; height: number }>;

/**
 * Control sizes in CSS pixels; the interface renders at these exact values.
 * Widths and the height are even so that centring a pill on the orb lands on
 * a whole pixel.
 */
export const ORB_LAYOUT = Object.freeze({
  /** Space between the orb's rim and the nearest edge of each pill. */
  pillGap: 8,
  pillHeight: 38,
  /** Mute or Unmute (left) and Pause or Resume (right). */
  sidePillWidth: 124,
  /** Return to session (above) and End interview (below). */
  stackPillWidth: 188,
  /** The hard ink shadow under the orb and the pills. */
  shadow: 3,
  /** Clear viewport kept around the expanded controls and the confirmation. */
  edgeInset: 8,
  confirmationWidth: 272,
});

/** Offsets from the orb's centre to the centre of each expanded pill. */
export type PillOffsets = Readonly<{ stack: number; side: number }>;

export function orbRadius(diameter: number): number {
  return Math.round(diameter / 2);
}

export function pillOffsets(diameter: number): PillOffsets {
  const radius = orbRadius(diameter);
  return {
    stack: radius + ORB_LAYOUT.pillGap + ORB_LAYOUT.pillHeight / 2,
    side: radius + ORB_LAYOUT.pillGap + ORB_LAYOUT.sidePillWidth / 2,
  };
}

export function horizontalMargin(diameter: number): number {
  const reach = Math.max(
    orbRadius(diameter) + ORB_LAYOUT.pillGap + ORB_LAYOUT.sidePillWidth,
    ORB_LAYOUT.stackPillWidth / 2,
  );
  return reach + ORB_LAYOUT.shadow + ORB_LAYOUT.edgeInset;
}

export function verticalMargin(diameter: number): number {
  const reach = orbRadius(diameter) + ORB_LAYOUT.pillGap + ORB_LAYOUT.pillHeight;
  return reach + ORB_LAYOUT.shadow + ORB_LAYOUT.edgeInset;
}

/**
 * Turns the stored ratios into a whole-pixel centre inside the current
 * viewport. A viewport too small for the expanded pills centres the orb on
 * that axis rather than letting a control leave the screen.
 */
export function orbCentre(settings: OrbSettings, viewport: Viewport): Point {
  return {
    x: Math.round(
      clampAxis(settings.anchorXRatio * viewport.width, viewport.width, horizontalMargin(settings.diameter)),
    ),
    y: Math.round(
      clampAxis(settings.anchorYRatio * viewport.height, viewport.height, verticalMargin(settings.diameter)),
    ),
  };
}

/** Converts a dragged centre point back into device-local ratios. */
export function orbAnchor(centre: Point, settings: OrbSettings, viewport: Viewport): OrbSettings {
  const clamped = {
    x: clampAxis(centre.x, viewport.width, horizontalMargin(settings.diameter)),
    y: clampAxis(centre.y, viewport.height, verticalMargin(settings.diameter)),
  };
  return {
    anchorXRatio: viewport.width === 0 ? 0 : clamp(clamped.x / viewport.width, 0, 1),
    anchorYRatio: viewport.height === 0 ? 0 : clamp(clamped.y / viewport.height, 0, 1),
    diameter: settings.diameter,
  };
}

/**
 * Places the End confirmation where the End pill was, below the orb, or above
 * it when the viewport has no room below. It is centred on the orb and kept
 * inside the viewport horizontally. Returns the card's top-left corner in
 * viewport coordinates.
 */
export function confirmationOrigin(
  centre: Point,
  diameter: number,
  card: Size,
  viewport: Viewport,
): Point {
  const radius = orbRadius(diameter);
  const inset = ORB_LAYOUT.edgeInset;
  const below = centre.y + radius + ORB_LAYOUT.pillGap;
  const above = centre.y - radius - ORB_LAYOUT.pillGap - card.height;
  const fitsBelow = below + card.height + ORB_LAYOUT.shadow + inset <= viewport.height;
  const y = fitsBelow ? below : Math.max(above, inset);
  const x = clamp(
    centre.x - card.width / 2,
    inset,
    Math.max(inset, viewport.width - inset - ORB_LAYOUT.shadow - card.width),
  );
  return { x: Math.round(x), y: Math.round(y) };
}

/** A whole-pixel CSS length. */
export function px(value: number): string {
  return `${String(value)}px`;
}

function clampAxis(value: number, extent: number, margin: number): number {
  if (extent <= margin * 2) {
    return extent / 2;
  }
  return clamp(value, margin, extent - margin);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}
