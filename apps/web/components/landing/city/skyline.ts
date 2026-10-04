import { iso, type Box, type Pt } from './iso';
import type { Item } from './model';
import { CITY_PLAN } from './plan';
import { CRANE } from './props';
import { FIGURES, PLANTS, spriteRise } from './sprites';

/** How far above its tail a speech bubble reaches as it floats, in design pixels. */
const BUBBLE_REACH = 110;

/** Points along the top of an item's silhouette, in design pixels. */
export function silhouette(item: Item): readonly Pt[] {
  const b: Box = item.box;
  const foot = iso([b.x + b.w / 2, b.y + b.d / 2, 0]);
  const above = (px: number): readonly Pt[] => [[foot[0] - 14, foot[1] - px], [foot[0], foot[1] - px], [foot[0] + 14, foot[1] - px]];
  switch (item.kind) {
    case 'tree': return above(spriteRise(PLANTS[item.plant]) * item.size);
    case 'lamp': return above(92);
    case 'person': return above(Math.max(...FIGURES[item.figure].map(spriteRise)));
    case 'agent': return above(item.size * 0.72);
    case 'crane': {
      const points: Pt[] = [iso([b.x + b.w / 2, b.y + b.d / 2, b.h - 0.2 + CRANE.cab])];
      for (let t = -CRANE.back; t <= item.jib; t += 0.5) points.push(iso([b.x + b.w / 2 + t, b.y + b.d / 2, b.h + 0.6]));
      return points;
    }
    case 'building': {
      // the roof with its furniture: rooftop units reach 0.8 units above it, a water tank 2.6
      const furniture = item.roof === 'tank' ? 2.6 : item.roof === 'units' ? 0.8 : 0;
      const points: Pt[] = [];
      for (let u = 0; u <= 1; u += 0.125) for (let v = 0; v <= 1; v += 0.125) points.push(iso([b.x + b.w * u, b.y + b.d * v, b.z + b.h + furniture]));
      return points;
    }
    default: {
      const points: Pt[] = [];
      for (let u = 0; u <= 1; u += 0.125) for (let v = 0; v <= 1; v += 0.125) points.push(iso([b.x + b.w * u, b.y + b.d * v, b.z + b.h]));
      return points;
    }
  }
}

/** The tops of everything in the city, bubbles at their highest float included; the clouds hang on their own. */
const TOPS: readonly Pt[] = [
  ...CITY_PLAN.items.flatMap((item) => silhouette(item)),
  ...CITY_PLAN.bubbles.map((bubble): Pt => { const [x, y] = iso(bubble.at); return [x, y - BUBBLE_REACH]; }),
];

/** The highest point (the least design y) of anything in the city between design x `from` and `to`. */
export function skylineTop(from: number, to: number): number {
  const seen = TOPS.filter(([x]) => x >= from && x <= to).map(([, y]) => y);
  if (seen.length === 0) throw new RangeError(`The city has nothing between design x ${from.toFixed(0)} and ${to.toFixed(0)} to measure a skyline from.`);
  return Math.min(...seen);
}
