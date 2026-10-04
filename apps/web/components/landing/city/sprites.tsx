import type { CSSProperties, JSX } from 'react';

import type { Pt } from './iso';

/**
 * Hand-drawn pieces of the city: the people, the plants and the clouds, drawn with GPT Image 2 in
 * the chosen reference's style, traced to SVG with vectorizer.ai, and served from
 * `public/landing/city/`. Each entry records its viewBox, its anchor (feet or trunk base, in source
 * units) and the scale that puts it at city size in design pixels. People have two frames that the
 * page alternates on its own beat.
 */
type Sprite = Readonly<{ src: string; width: number; height: number; ax: number; ay: number; scale: number }>;

const sprite = (src: string, width: number, height: number, ax: number, ay: number, scale: number): Sprite => ({ src: `/landing/city/${src}.svg`, width, height, ax, ay, scale });

export const FIGURES = Object.freeze({
  'customer-seated': [sprite('people/customer-seated-0', 652, 870, 326, 854, 0.09), sprite('people/customer-seated-1', 652, 870, 326, 854, 0.09)],
  'customer-standing': [sprite('people/customer-standing-0', 446, 980, 223, 964, 0.1058), sprite('people/customer-standing-1', 446, 980, 223, 964, 0.1058)],
  reader: [sprite('people/reader-0', 678, 944, 339, 928, 0.08), sprite('people/reader-1', 678, 944, 339, 928, 0.08)],
} as const);
export type Figure = keyof typeof FIGURES;

export const PLANTS = Object.freeze({
  round: sprite('flora/round', 373, 458, 189, 431, 0.247),
  cone: sprite('flora/cone', 278, 466, 135, 439, 0.223),
  shrub: sprite('flora/shrub', 321, 236, 160, 209, 0.219),
  bush: sprite('flora/bush', 200, 196, 100, 169, 0.238),
  grass: sprite('flora/grass', 174, 201, 88, 174, 0.149),
});
export type Plant = keyof typeof PLANTS;

export const CLOUDS = Object.freeze({
  wide: sprite('clouds/cloud-wide', 519, 377, 259, 377, 0.366),
  tall: sprite('clouds/cloud-tall', 539, 397, 269, 397, 0.371),
  small: sprite('clouds/cloud-small', 326, 249, 163, 249, 0.368),
});
export type Cloud = keyof typeof CLOUDS;

/** How tall a sprite stands above its anchor, in design pixels. */
export function spriteRise(s: Sprite): number {
  return s.ay * s.scale;
}

/** One traced drawing placed with its anchor on a design point; `flip` mirrors it about the anchor. */
export function SpriteImage(props: Readonly<{ sprite: Sprite; at: Pt; flip: boolean; className?: string; style?: CSSProperties }>): JSX.Element {
  const { sprite: s } = props;
  const w = s.width * s.scale;
  const h = s.height * s.scale;
  const x = props.at[0] - s.ax * s.scale;
  const y = props.at[1] - s.ay * s.scale;
  return <image
    href={s.src}
    x={x.toFixed(2)}
    y={y.toFixed(2)}
    width={w.toFixed(2)}
    height={h.toFixed(2)}
    className={props.className}
    style={props.style}
    transform={props.flip ? `translate(${(2 * props.at[0]).toFixed(2)} 0) scale(-1 1)` : undefined}
  />;
}
