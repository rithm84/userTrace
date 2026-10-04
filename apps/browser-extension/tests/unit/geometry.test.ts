import { describe, expect, it } from "vitest";

import {
  ORB_LAYOUT,
  confirmationOrigin,
  horizontalMargin,
  orbAnchor,
  orbCentre,
  pillOffsets,
  verticalMargin,
} from "../../src/content/orbGeometry";
import {
  MINIMUM_VISIBLE_FRACTION,
  centreOf,
  checkTarget,
  circlePath,
  labelBox,
  tracePath,
} from "../../src/content/sceneGeometry";
import type { ResolvedTarget } from "../../src/protocol/bridge";
import { DEFAULT_ORB_SETTINGS, ORB_DIAMETER, type OrbSettings } from "../../src/shared/portMessages";

const VIEWPORT = { width: 1280, height: 760 };
const SETTINGS: OrbSettings = DEFAULT_ORB_SETTINGS;
const DIAMETERS: readonly number[] = Array.from(
  { length: (ORB_DIAMETER.max - ORB_DIAMETER.min) / ORB_DIAMETER.step + 1 },
  (_value, index) => ORB_DIAMETER.min + index * ORB_DIAMETER.step,
);

function target(overrides: Partial<ResolvedTarget>): ResolvedTarget {
  return {
    target_id: "tgt_save_view_button",
    target_revision: 7,
    layout_revision: 11,
    frame_id: "frm_top",
    rect: { x: 412, y: 152, width: 128, height: 36 },
    visible_fraction: 1,
    occluded: false,
    in_viewport: true,
    supports_visual_guidance: true,
    ...overrides,
  };
}

describe("orb placement", () => {
  it("keeps the expanded pills inside the viewport on every edge", () => {
    const corners: readonly OrbSettings[] = [
      { ...SETTINGS, anchorXRatio: 0, anchorYRatio: 0 },
      { ...SETTINGS, anchorXRatio: 1, anchorYRatio: 1 },
    ];
    for (const corner of corners) {
      const centre = orbCentre(corner, VIEWPORT);
      expect(centre.x).toBeGreaterThanOrEqual(horizontalMargin(corner.diameter));
      expect(centre.x).toBeLessThanOrEqual(VIEWPORT.width - horizontalMargin(corner.diameter));
      expect(centre.y).toBeGreaterThanOrEqual(verticalMargin(corner.diameter));
      expect(centre.y).toBeLessThanOrEqual(VIEWPORT.height - verticalMargin(corner.diameter));
    }
  });

  it("reserves room for the side pills, the stacked pills, their shadow, and an inset", () => {
    const clearance = ORB_LAYOUT.shadow + ORB_LAYOUT.edgeInset;
    expect(horizontalMargin(120)).toBe(60 + ORB_LAYOUT.pillGap + ORB_LAYOUT.sidePillWidth + clearance);
    expect(verticalMargin(120)).toBe(60 + ORB_LAYOUT.pillGap + ORB_LAYOUT.pillHeight + clearance);
  });

  it("puts the orb and every pill on whole pixels so nothing is resampled", () => {
    for (const diameter of DIAMETERS) {
      const offsets = pillOffsets(diameter);
      expect(Number.isInteger(offsets.stack)).toBe(true);
      expect(Number.isInteger(offsets.side)).toBe(true);
      for (const viewport of [VIEWPORT, { width: 1437, height: 811 }, { width: 333, height: 257 }]) {
        const centre = orbCentre({ anchorXRatio: 0.613, anchorYRatio: 0.377, diameter }, viewport);
        expect(Number.isInteger(centre.x)).toBe(true);
        expect(Number.isInteger(centre.y)).toBe(true);
      }
    }
  });

  it("centres the orb on an axis too small for its controls", () => {
    const centre = orbCentre(SETTINGS, { width: 200, height: 120 });
    expect(centre).toStrictEqual({ x: 100, y: 60 });
  });

  it("round-trips a dragged centre into device-local ratios", () => {
    const dragged = { x: 640, y: 380 };
    const anchored = orbAnchor(dragged, SETTINGS, VIEWPORT);
    expect(orbCentre(anchored, VIEWPORT)).toStrictEqual(dragged);
  });

  it("asks to end below the orb, or above it when the viewport has no room below", () => {
    const card = { width: ORB_LAYOUT.confirmationWidth, height: 132 };
    const below = confirmationOrigin({ x: 640, y: 200 }, 120, card, VIEWPORT);
    expect(below).toStrictEqual({ x: 640 - card.width / 2, y: 200 + 60 + ORB_LAYOUT.pillGap });

    const above = confirmationOrigin({ x: 640, y: 640 }, 120, card, VIEWPORT);
    expect(above.y + card.height).toBe(640 - 60 - ORB_LAYOUT.pillGap);

    const atEdge = confirmationOrigin({ x: 1200, y: 200 }, 120, card, VIEWPORT);
    expect(atEdge.x + card.width + ORB_LAYOUT.shadow).toBeLessThanOrEqual(
      VIEWPORT.width - ORB_LAYOUT.edgeInset,
    );
  });

});

describe("target checks before execution", () => {
  it("accepts a fully visible registered visual target", () => {
    expect(checkTarget(target({}), VIEWPORT)).toStrictEqual({ usable: true });
  });

  it.each([
    ["target_not_visual", { supports_visual_guidance: false }],
    ["target_degenerate", { rect: { x: 10, y: 10, width: 0, height: 20 } }],
    ["target_offscreen", { in_viewport: false }],
    ["target_obscured", { occluded: true }],
    ["target_obscured", { visible_fraction: MINIMUM_VISIBLE_FRACTION - 0.01 }],
  ])("refuses with %s", (reason, overrides) => {
    expect(checkTarget(target(overrides), VIEWPORT)).toStrictEqual({ usable: false, reason });
  });
});

describe("marks derived from target geometry", () => {
  it("draws the loop around the padded target box", () => {
    const rect = { x: 100, y: 100, width: 200, height: 100 };
    const path = circlePath(rect, 8);
    const numbers = [...path.matchAll(/-?\d+(\.\d+)?/gu)].map((match) => Number(match[0]));
    const xs = numbers.filter((_value, index) => index % 2 === 0);
    const ys = numbers.filter((_value, index) => index % 2 === 1);
    expect(Math.min(...xs)).toBeCloseTo(92, 5);
    expect(Math.max(...xs)).toBeCloseTo(308, 5);
    expect(Math.min(...ys)).toBeCloseTo(92, 5);
    expect(Math.max(...ys)).toBeCloseTo(208, 5);
    expect(centreOf(rect)).toStrictEqual({ x: 200, y: 150 });
  });

  it("traces the requested edge", () => {
    const rect = { x: 100, y: 100, width: 200, height: 100 };
    expect(tracePath(rect, "top", 4)).toBe("M 96 96 Q 200 92 304 96");
    expect(tracePath(rect, "leading", 4)).toBe("M 96 96 Q 92 150 96 204");
  });

  it("honours the requested label placement when it fits", () => {
    const rect = { x: 400, y: 300, width: 120, height: 40 };
    expect(labelBox(rect, "above", { width: 160, height: 28 }, VIEWPORT, 8)).toStrictEqual({
      x: 380,
      y: 264,
      width: 160,
      height: 28,
    });
  });

  it("flips a label that would leave the viewport and clamps it inside", () => {
    const rect = { x: 400, y: 4, width: 120, height: 40 };
    const box = labelBox(rect, "above", { width: 160, height: 28 }, VIEWPORT, 8);
    expect(box.y).toBe(52);
    expect(box.x + box.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  it("keeps a label beside a target at the right edge inside the viewport", () => {
    const rect = { x: 1200, y: 300, width: 70, height: 40 };
    const box = labelBox(rect, "trailing", { width: 160, height: 28 }, VIEWPORT, 8);
    expect(box.x).toBe(1032);
    expect(box.x + box.width).toBeLessThanOrEqual(VIEWPORT.width);
  });
});
