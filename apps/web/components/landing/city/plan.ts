import type { AgentColour } from '../characters/AgentSprite';
import type { Box } from './iso';
import type { AgentCue, AppKind, Bubble, CityPlan, Facade, Ground, Item, Roof, Sky } from './model';
import type { Tint } from './paint';
import type { Figure, Plant } from './sprites';

/**
 * The hero's city, laid out in ground units around a V of two streets whose apex sits under the
 * copy. The left arm runs from the feedback tower down to the building site; the right arm climbs
 * to the shop where the customer validates. Everything is placed so that no roof, crane or bubble
 * rises into the copy above it (see `KEEP_OUT` in `fit.ts`).
 */

const box = (x: number, y: number, z: number, w: number, d: number, h: number): Box => ({ x, y, z, w, d, h });
/** A small footprint centred on a ground point. */
const spot = (x: number, y: number, size: number, h: number): Box => box(x - size / 2, y - size / 2, 0, size, size, h);

function building(id: string, b: Box, tint: Tint, left: Facade, right: Facade, roof: Roof): Item {
  return { kind: 'building', id, box: b, tint, left, right, roof };
}
const windows = (...lit: number[]): Facade => ({ kind: 'windows', lit, door: false });
const entrance = (...lit: number[]): Facade => ({ kind: 'windows', lit, door: true });
const app = (name: AppKind): Facade => ({ kind: 'app', app: name });

/** A plant at `size` times its drawn height (1 is a full street tree). */
const plant = (id: string, kind: Plant, x: number, y: number, flip: boolean, size: number): Item => ({ kind: 'tree', id, box: spot(x, y, kind === 'grass' || kind === 'bush' ? 0.6 : 1.2, (kind === 'grass' ? 1 : kind === 'bush' || kind === 'shrub' ? 1.8 : 5) * size), plant: kind, size, flip });
const lamp = (id: string, x: number, y: number): Item => ({ kind: 'lamp', id, box: spot(x, y, 0.6, 4.5) });
/** A figure's tempo is how long each of its two drawn frames holds, in milliseconds; a cued figure waits for the story instead. */
const person = (id: string, figure: Figure, x: number, y: number, tempoMs: number, offsetMs: number): Item => ({ kind: 'person', id, box: spot(x, y, 1, 4.6), figure, flip: false, tempoMs, offsetMs, cued: false });
const cuedPerson = (id: string, figure: Figure, x: number, y: number): Item => ({ kind: 'person', id, box: spot(x, y, 1, 4.6), figure, flip: false, tempoMs: 0, offsetMs: 0, cued: true });
const agent = (id: string, colour: AgentColour, x: number, y: number, cue: AgentCue): Item => ({ kind: 'agent', id, box: spot(x, y, 2.4, 4.6), colour, size: RIG_SIZE, cue });
/** Rig box size in design pixels; the body fills about half of it. */
export const RIG_SIZE = 112;

const ground: readonly Ground[] = [
  // the V: the left arm runs down-right to the apex, the right arm climbs up-right from it
  { kind: 'street', x: -60, y: 14, w: 94, d: 4, along: 'x', lanes: false },
  { kind: 'street', x: 14, y: -60, w: 4, d: 74, along: 'y', lanes: false },
  { kind: 'street', x: 14, y: 18, w: 4, d: 40, along: 'y', lanes: true },
  { kind: 'street', x: 34, y: 14, w: 26, d: 4, along: 'x', lanes: true },
  { kind: 'street', x: -26, y: 18, w: 4, d: 40, along: 'y', lanes: true },
  { kind: 'street', x: 18, y: -26, w: 40, d: 4, along: 'x', lanes: true },
  { kind: 'path', x: -33, y: 15.1, w: 49.9, d: 1.8, along: 'x', sign: 1 },
  { kind: 'path', x: 15.1, y: -36, w: 1.8, d: 52.9, along: 'y', sign: -1 },
  { kind: 'crossing', x: 11.9, y: 14, w: 1.8, d: 4, along: 'y' },
  { kind: 'crossing', x: 14, y: 11.9, w: 4, d: 1.8, along: 'x' },
  { kind: 'crossing', x: 18.3, y: 14, w: 1.8, d: 4, along: 'y' },
  { kind: 'crossing', x: 14, y: 18.3, w: 4, d: 1.8, along: 'x' },
  { kind: 'crossing', x: -26, y: 18.3, w: 4, d: 1.8, along: 'x' },
  { kind: 'crossing', x: 18.3, y: -26, w: 1.8, d: 4, along: 'y' },
  // inside the V: tall ground beside the copy, low lawns under it, the building site at the apex
  { kind: 'slab', x: -60, y: 7.5, w: 29, d: 6.5 },
  { kind: 'slab', x: -31, y: 9, w: 36.5, d: 5 },
  { kind: 'slab', x: 5.5, y: 5.5, w: 8.5, d: 8.5 },
  { kind: 'slab', x: 7.5, y: -60, w: 6.5, d: 29 },
  { kind: 'slab', x: 9, y: -31, w: 5, d: 36.5 },
  { kind: 'lot', x: 6.3, y: 6.3, w: 6.9, d: 6.9 },
  { kind: 'lawn', x: -24, y: 10, w: 18, d: 2.6 },
  { kind: 'lawn', x: 10, y: -24, w: 2.6, d: 27 },
  // outside the V
  { kind: 'slab', x: -60, y: 18, w: 34, d: 42 },
  { kind: 'slab', x: -22, y: 18, w: 36, d: 42 },
  { kind: 'slab', x: 18, y: -60, w: 42, d: 34 },
  { kind: 'slab', x: 18, y: -22, w: 42, d: 36 },
  { kind: 'slab', x: 18, y: 18, w: 16, d: 40 },
  { kind: 'slab', x: 34, y: 18, w: 26, d: 40 },
  // parks behind the single row of shops on each side, and a green under the apex
  { kind: 'lawn', x: -20, y: 30, w: 31, d: 15 },
  // the café's terrace runs out from the café's shopfront (x -14), leaving a strip of pavement
  // before the next shop, ends level with the café's back wall (y 27) and runs forward to the kerb
  { kind: 'deck', x: -14, y: 18.1, w: 7.6, d: 8.9 },
  { kind: 'lawn', x: 30, y: -20, w: 15, d: 31 },
  { kind: 'lawn', x: 20.5, y: 20.5, w: 11, d: 11 },
];

const items: readonly Item[] = [
  // the feedback tower: a chat app where customers talk, lit windows round the side, and a plain
  // roof, so the feedback bubbles rise over a clean roofline
  building('chat-tower', box(-37.5, 7.5, 0, 6, 5, 9), 'lilac', app('chat'), entrance(1, 4, 6), 'plain'),
  building('gallery-left', box(-47, 7.5, 0, 8, 5, 9), 'peach', app('gallery'), entrance(2), 'units'),
  agent('coral', 'coral', -30, 13.3, 'investigate'),
  lamp('lamp-l1', -27, 13.4),
  // the lawn under the left arm, with a man reading on the bench
  plant('tree-l1', 'round', -20, 11.5, false, 0.72),
  plant('tree-l2', 'round', -15, 11.5, false, 0.9),
  plant('tree-l3', 'cone', -7, 11.5, true, 0.74),
  plant('grass-l1', 'grass', -22.5, 11.2, false, 1),
  plant('bush-l1', 'bush', -9.8, 11.3, false, 1),
  { kind: 'bench', id: 'bench-l1', box: box(-17, 12.6, 0, 2.4, 0.8, 1.9), along: 'x' },
  person('reader', 'reader', -15.6, 13.75, 2600, 0),
  lamp('lamp-l2', -2, 13.4),
  // the café holds the block's corner on the row's front line near the road (y 21 to 27, like the
  // shops beside it); its shopfront and full-length awning face the terrace, where sky interviews
  // the customer at the table by the kerb and a second table sits further back
  building('cafe', box(-21, 21, 0, 7, 6, 4.4), 'peach', windows(), { kind: 'shop', awning: 'coral', app: null }, 'units'),
  { kind: 'table', id: 'cafe-table', box: spot(-10.7, 20.6, 1.2, 1.4) },
  person('customer-cafe', 'customer-seated', -12.2, 22.1, 900, 0),
  agent('sky', 'sky', -9.3, 19.4, 'interview'),
  { kind: 'table', id: 'cafe-table-2', box: spot(-8.4, 24.4, 1.2, 1.4) },
  // street works between the café and the site: aqua climbs out of a manhole with the pain point under the complaint
  // in the middle of the road, where the gap between the two shops leaves the hole in clear view
  { kind: 'dig', id: 'dig', box: spot(1.5, 16, 2.8, 0.2) },
  agent('aqua', 'aqua', 1.5, 16, 'uncover'),
  plant('tree-f3', 'round', 12.4, 19.6, false, 1),
  // outside the left arm: the café on its corner, its terrace, and two low shops, all on one front
  // line, with a park behind them
  building('front-left-a', box(-44, 21, 0, 7, 6, 4), 'coral', entrance(3), app('profile'), 'units'),
  building('front-left-b', box(-36, 21, 0, 8, 6, 4), 'sky', app('form'), entrance(), 'plain'),
  building('front-left-d', box(-4, 21, 0, 7, 6, 4), 'mint', windows(2), entrance(), 'plain'),
  building('front-left-e', box(7, 21, 0, 6, 6, 4), 'lilac', entrance(1), app('dashboard'), 'plain'),
  // the park behind the left row: two loose rows of trees about 7 units apart, bushes in the gaps
  plant('park-l1', 'round', -17.5, 32.8, false, 0.9),
  plant('park-l2', 'cone', -10.3, 32.2, true, 0.8),
  plant('park-l3', 'round', -2.6, 33.1, true, 0.95),
  plant('park-l4', 'cone', 4.3, 32.4, false, 0.85),
  plant('park-l5', 'cone', -13.8, 39.4, false, 0.85),
  plant('park-l6', 'round', -6.2, 38.6, true, 0.85),
  plant('park-l7', 'cone', 0.9, 39.5, true, 0.8),
  plant('park-l8', 'round', 7.2, 38.8, false, 0.95),
  plant('park-l9', 'bush', -13.9, 35.9, false, 1),
  plant('park-l10', 'bush', 1.2, 36.1, true, 1),
  // the building site inside the V: the frame going up on its gravel lot, unfenced so nothing
  // crosses the building
  { kind: 'frame', id: 'frame', box: box(8.8, 8.2, 0, 3.6, 3.6, 3.3) },
  // the crane stands just in front of the frame's facade, so its jib passes over the frame
  { kind: 'crane', id: 'crane', box: box(3.2, 11.8, 0, 1, 1, 6), jib: 9.1 },
  { kind: 'load', id: 'crane-load', box: box(5.4, 12.1, 1.9, 6, 0.4, 3.45) },
  agent('black', 'black', 7.6, 12.8, 'build'),
  // the right arm up to the shop where the customer validates
  building('dash-tower', box(7.5, -37.5, 0, 5, 6, 9), 'aqua', windows(0, 5, 7), { kind: 'shop', awning: 'mint', app: 'dashboard' }, 'units'),
  building('gallery-right', box(7.5, -47, 0, 5, 8, 9), 'lilac', windows(3), app('profile'), 'plain'),
  cuedPerson('customer-shop', 'customer-standing', 13.3, -34.5),
  agent('lilac', 'lilac', 13.35, -31.6, 'validate'),
  plant('tree-r1', 'round', 11.5, -20, true, 0.72),
  plant('tree-r2', 'round', 11.5, -15, false, 0.9),
  plant('tree-r3', 'cone', 11.5, -7, false, 0.74),
  plant('grass-r1', 'grass', 11.2, -22.5, true, 1),
  plant('bush-r1', 'bush', 11.3, -9.8, true, 1),
  { kind: 'bench', id: 'bench-r1', box: box(12.6, -17, 0, 0.8, 2.4, 1.9), along: 'y' },
  lamp('lamp-r2', 13.4, -2),
  plant('tree-r7', 'round', 11.4, 1.2, true, 0.8),
  // mint parks past the street tree at y -18, so the tree never covers the rider
  { kind: 'trike', id: 'trike', box: box(16.1, -27.2, 0, 1.8, 39.7, 3.2), route: [11, -24] },
  // outside the right arm
  // street trees in the gaps between the right row's shops
  plant('tree-r4', 'round', 19.6, -18, false, 1),
  plant('tree-r5', 'cone', 19.6, -5.5, true, 1),
  plant('tree-r6', 'round', 19.6, 4.5, true, 1),
  building('front-right-a', box(21, -44, 0, 6, 7, 4), 'mint', entrance(), windows(4), 'units'),
  building('front-right-b', box(21, -36, 0, 6, 8, 4), 'peach', entrance(), app('form'), 'plain'),
  // the right row stands on the same front line as the left one, 3 units back from its kerb (x 21)
  building('front-right-c', box(21, -14, 0, 6, 7, 4), 'lilac', entrance(), windows(1), 'units'),
  building('front-right-d', box(21, -4, 0, 6, 7, 4), 'coral', entrance(), windows(1), 'units'),
  building('front-right-e', box(21, 6, 0, 6, 7, 4), 'mint', app('dashboard'), entrance(1), 'plain'),
  // the park behind the right row mirrors the left one across the V
  plant('park-r1', 'round', 32.8, -17.5, true, 0.9),
  plant('park-r2', 'cone', 32.2, -10.3, false, 0.8),
  plant('park-r3', 'round', 33.1, -2.6, false, 0.95),
  plant('park-r4', 'cone', 32.4, 4.3, true, 0.85),
  plant('park-r5', 'cone', 39.4, -13.8, true, 0.85),
  plant('park-r6', 'round', 38.6, -6.2, false, 0.85),
  plant('park-r7', 'cone', 39.5, 0.9, false, 0.8),
  plant('park-r8', 'round', 38.8, 7.2, true, 0.95),
  plant('park-r9', 'bush', 35.9, -13.9, true, 1),
  plant('park-r10', 'bush', 36.1, 1.2, false, 1),
  // below the apex: a small green with its trees, instead of blocks
  plant('tree-c1', 'round', 19.4, 19.4, false, 1),
  plant('tree-c2', 'cone', 29.6, 19.4, true, 1),
  plant('tree-c3', 'round', 24.5, 26.5, true, 0.95),
  plant('bush-c1', 'bush', 28.5, 24, false, 1),
];

const bubbles: readonly Bubble[] = [
  // the three feedback bubbles leave the chat window side by side, 48 px apart on one level, so as
  // they float straight up they never meet
  { id: 'feed-1', at: [-36.8, 13.2, 5.2], glyph: 'bug', cue: 'feed' },
  { id: 'feed-2', at: [-34, 13.2, 6.6], glyph: 'star', cue: 'feed' },
  { id: 'feed-3', at: [-31.2, 13.2, 8], glyph: 'question', cue: 'feed' },
  { id: 'customer', at: [-12.2, 22.1, 4.6], glyph: 'lines', cue: 'customer' },
  { id: 'pain', at: [1.5, 16, 4.4], glyph: 'alert', cue: 'pain' },
  { id: 'reply', at: [13.3, -34.5, 5.6], glyph: 'heart', cue: 'reply' },
];

/**
 * Where a cloud may reach, drift included, measured against the copy from 1024 px wide up: below
 * y 100 (the headline's second line and the lede, at their highest) no nearer the centre than
 * x 620 or 1780; above it (the first line) no nearer than x 712 or 1688.
 */
export const SKY_CLEAR = Object.freeze({ lowY: 100, low: [620, 1780] as const, high: [712, 1688] as const });

/** The most a cloud swells as it breathes, about its base centre. */
export const SKY_SWELL = 1.02;

/**
 * Three clouds a side, in design pixels (bottom centre), as they sit on a narrow stage; a wider
 * stage crops the city's top under the navbar, so the city hangs them lower until the highest
 * clears the navbar (`skyDrop` in City.tsx). Each side is a loose triangle that never overlaps
 * itself: a small puff and a big cloud side by side on top, about 22 px apart, and a medium cloud
 * under the gap, leaning outward, about 9 px below; the gaps outlast every cloud's drift and bob. Every one
 * stays clear of the copy at every width (`SKY_CLEAR`).
 */
const sky: readonly Sky[] = [
  { cloud: 'small', at: [452, 72], size: 0.75, drift: 5 },
  { cloud: 'wide', at: [604, 84], size: 0.9, drift: 8 },
  { cloud: 'tall', at: [526, 184], size: 0.62, drift: 6 },
  { cloud: 'small', at: [1948, 72], size: 0.75, drift: 5 },
  { cloud: 'wide', at: [1796, 84], size: 0.9, drift: 8 },
  { cloud: 'tall', at: [1874, 184], size: 0.62, drift: 6 },
];

export const CITY_PLAN: CityPlan = { ground, items, bubbles, sky };
