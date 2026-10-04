import type { JSX } from 'react';

import { cycleAttributes } from './cycles';
import type { Pt } from './iso';
import { GroundShadow } from './props';
import { FIGURES, SpriteImage, type Figure } from './sprites';

/**
 * A customer in the city: a traced drawing in two frames, feet on the ground point. Ambient people
 * alternate their frames at their own tempo (an explaining hand), switched by the city's clock; a
 * `cued` person holds the first frame until the story turns them on (`is-on`, toggled by the
 * city's clock through `data-cue`), as the customer's thumbs-up does when the fix reaches her.
 */
export function Person(props: Readonly<{ at: Pt; figure: Figure; flip: boolean; tempoMs: number; offsetMs: number; cued: boolean }>): JSX.Element {
  const [first, second] = FIGURES[props.figure];
  const shadow = <GroundShadow at={props.at} rx={props.figure === 'customer-seated' ? 22 : 15} />;
  if (props.cued) {
    return <g data-cue="person">
      {shadow}
      <SpriteImage sprite={first} at={props.at} flip={props.flip} className="city-cue-a" />
      <SpriteImage sprite={second} at={props.at} flip={props.flip} className="city-cue-b" />
    </g>;
  }
  const frame = (index: number) => cycleAttributes({ period: props.tempoMs * 2, offset: props.offsetMs, from: index / 2, span: 0.5 });
  return <g>
    {shadow}
    <g {...frame(0)}><SpriteImage sprite={first} at={props.at} flip={props.flip} /></g>
    <g {...frame(1)}><SpriteImage sprite={second} at={props.at} flip={props.flip} /></g>
  </g>;
}
