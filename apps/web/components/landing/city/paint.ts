/**
 * The city's palette: black ink, white paper, and the cast's pastels on the buildings. Each
 * tint has a lit left face, a shaded right face (which also carries hatching) and a pale roof.
 */
export const INK = '#111111';
export const STREET = '#ecedf0';
export const STREET_LINE = '#c9ccd3';
export const GLASS = '#dcf0fd';
export const GLASS_LIT = '#ffe2c4';
export const CORAL_PATH = '#fcada3';
export const CORAL_STRONG = '#fa8d82';

/** Stroke weights in design pixels. */
export const W = Object.freeze({ heavy: 2.4, line: 1.8, fine: 1.2, hair: 0.9 });

const TINT_NAMES = ['white', 'coral', 'mint', 'sky', 'lilac', 'peach', 'aqua', 'grey'] as const;
export type Tint = (typeof TINT_NAMES)[number];

export type TintSet = Readonly<{ base: string; left: string; right: string; top: string }>;

const BASE: Readonly<Record<Tint, string>> = {
  white: '#ffffff',
  coral: '#fcada3',
  mint: '#97d6a4',
  sky: '#85cef9',
  lilac: '#cdb6f9',
  peach: '#f5b488',
  aqua: '#72d8d7',
  grey: '#d9dade',
};

function channel(hex: string, index: number): number {
  return parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16);
}

/** Linear mix of two #rrggbb colours; t = 0 gives a, 1 gives b. */
export function mix(a: string, b: string, t: number): string {
  const out = [0, 1, 2].map((i) => Math.round(channel(a, i) + (channel(b, i) - channel(a, i)) * t));
  return '#' + out.map((value) => value.toString(16).padStart(2, '0')).join('');
}

function tintSet(tint: Tint): TintSet {
  const base = BASE[tint];
  if (tint === 'white') return { base, left: '#f7f6f3', right: '#e4e3df', top: '#ffffff' };
  return { base, left: mix(base, '#ffffff', 0.32), right: mix(base, '#6b6b6b', 0.12), top: mix(base, '#ffffff', 0.62) };
}

export const TINTS: Readonly<Record<Tint, TintSet>> = Object.freeze(
  Object.fromEntries(TINT_NAMES.map((tint) => [tint, tintSet(tint)])) as Record<Tint, TintSet>,
);
