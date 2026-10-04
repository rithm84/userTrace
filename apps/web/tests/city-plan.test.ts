import { describe, expect, it } from 'vitest';

import { paintLevels } from '@/components/landing/city/depth';
import { cityFit, DESIGN, KEEP_OUT, NAV_CLEAR } from '@/components/landing/city/fit';
import { iso } from '@/components/landing/city/iso';
import { CITY_PLAN, SKY_CLEAR, SKY_SWELL } from '@/components/landing/city/plan';
import { silhouette, skylineTop } from '@/components/landing/city/skyline';
import { CLOUDS } from '@/components/landing/city/sprites';

describe('the hero city plan', () => {
  it('keeps every roof, rig, crane and bubble out of the copy above it', () => {
    const intrusions: string[] = [];
    const check = (id: string, [x, y]: Pt): void => {
      for (const zone of KEEP_OUT) {
        if (Math.abs(x - DESIGN.centre) < zone.dx && y < zone.y) intrusions.push(`${id} at (${x.toFixed(0)}, ${y.toFixed(0)}) is inside the copy zone ±${zone.dx} above ${zone.y}`);
      }
    };
    for (const item of CITY_PLAN.items) for (const point of silhouette(item)) check(item.id, point);
    for (const bubble of CITY_PLAN.bubbles) {
      const [x, y] = iso(bubble.at);
      check(bubble.id, [x, y - 110]);
    }
    expect(intrusions).toEqual([]);
  });

  it('keeps every cloud, as it drifts and swells, clear of the copy beside it', () => {
    const intrusions: string[] = [];
    for (const entry of CITY_PLAN.sky) {
      const sprite = CLOUDS[entry.cloud];
      const scale = sprite.scale * entry.size;
      const left = entry.at[0] - sprite.ax * scale * SKY_SWELL - entry.drift;
      const right = entry.at[0] + (sprite.width - sprite.ax) * scale * SKY_SWELL + entry.drift;
      const [inner, outer] = entry.at[1] > SKY_CLEAR.lowY ? SKY_CLEAR.low : SKY_CLEAR.high;
      if (right > inner && left < outer) intrusions.push(`${entry.cloud} cloud at (${entry.at.join(', ')}) reaches x ${left.toFixed(0)}-${right.toFixed(0)}`);
    }
    expect(intrusions).toEqual([]);
  });

  it('keeps the highest building the city shows clear of the navbar at every stage size', () => {
    // stage sizes and waitlist-bar bottoms measured in a browser, from a narrow laptop to a large display
    const stages: readonly (readonly [number, number, number])[] = [[1024, 696, 363], [1280, 728, 382], [1366, 696, 386], [1440, 828, 419], [1512, 873, 431], [1728, 1008, 461], [1920, 1008, 464], [1920, 1128, 470], [2560, 1368, 610], [390, 772, 380]];
    const touching: string[] = [];
    for (const [width, height, bar] of stages) {
      const fit = cityFit(width, height, bar, skylineTop);
      const highest = fit.top + skylineTop(-fit.left / fit.scale, (width - fit.left) / fit.scale) * fit.scale;
      if (highest < NAV_CLEAR - 0.5) touching.push(`${width} x ${height}: the skyline reaches ${highest.toFixed(1)} px under the navbar`);
    }
    expect(touching).toEqual([]);
  });

  it('paints in a consistent back-to-front order', () => {
    const nodes = CITY_PLAN.items.map((item) => ({ id: item.id, box: item.box, rig: item.kind === 'agent' }));
    expect(paintLevels(nodes, [])).toHaveLength(CITY_PLAN.items.length);
  });

  it('gives every item a unique id', () => {
    const ids = CITY_PLAN.items.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
