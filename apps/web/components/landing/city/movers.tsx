import type { JSX } from 'react';

import { Solid } from './buildings';
import { closedPath, faceRect, iso, leftFace, openPath, rightFace, type Box, type Pt } from './iso';
import type { Bubble } from './model';
import { CORAL_STRONG, INK, TINTS, W } from './paint';

/** A wheel standing in the vertical plane along gy, centred at (x, y, r). */
function wheel(x: number, y: number, r: number): string {
  const points: Pt[] = [];
  for (let i = 0; i < 24; i += 1) {
    const a = (i / 24) * Math.PI * 2;
    points.push(iso([x, y + Math.cos(a) * r, r + Math.sin(a) * r]));
  }
  return closedPath(points);
}

/** How high the rider sits, in ground units. */
export const TRIKE_SEAT = 0.95;

/**
 * The cargo trike riding up the right arm (toward -gy): the cargo box leads, the rider sits
 * behind it. `part` splits it around the rider: the far part is drawn before the rig, the
 * near part after, so the rig sits in the bike rather than on top of it.
 */
export function Trike(props: Readonly<{ x: number; y: number; part: 'far' | 'near' }>): JSX.Element {
  const { x, y } = props;
  if (props.part === 'near') {
    return <g>
      <path d={wheel(x + 0.5, y + 0.9, 0.55)} fill="#ffffff" stroke={INK} strokeWidth={W.line} />
      <path d={openPath([iso([x + 0.5, y + 0.9, 0.55]), iso([x + 0.5, y, TRIKE_SEAT - 0.1])])} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />
      <circle cx={iso([x + 0.5, y + 0.9, 0.55])[0]} cy={iso([x + 0.5, y + 0.9, 0.55])[1]} r={2} fill={INK} />
    </g>;
  }
  const cargo: Box = { x: x - 0.9, y: y - 3, z: 0.75, w: 1.8, d: 1.9, h: 1.25 };
  const side = rightFace(cargo);
  const front = leftFace(cargo);
  const [sx, sy] = iso([x, y + 0.2, TRIKE_SEAT]);
  return <g>
    <ellipse cx={iso([x, y - 1.2, 0])[0]} cy={iso([x, y - 1.2, 0])[1]} rx={30} ry={13} fill="rgba(17,17,17,0.12)" />
    <path d={wheel(x - 0.7, y - 2.1, 0.5)} fill="#ffffff" stroke={INK} strokeWidth={W.line} />
    <Solid box={cargo} tint="white" weight={W.line} />
    <path d={faceRect(side, 0, 0.45, side.width, 0.35)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.hair} />
    <path d={faceRect(front, 0, 0.45, front.width, 0.35)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.hair} />
    <path d={wheel(x + 0.95, y - 2.1, 0.5)} fill="#ffffff" stroke={INK} strokeWidth={W.line} />
    <path d={openPath([iso([x + 0.3, y - 1.05, 1.1]), iso([x + 0.3, y - 0.55, 2.35])])} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />
    <path d={openPath([iso([x - 0.35, y - 0.55, 2.35]), iso([x + 0.95, y - 0.55, 2.35])])} stroke={INK} strokeWidth={2.4} strokeLinecap="round" />
    <path d={`M${sx - 9} ${sy}L${sx + 9} ${sy}`} stroke={INK} strokeWidth={4} strokeLinecap="round" />
  </g>;
}

function Glyph(props: Readonly<{ glyph: Bubble['glyph']; x: number; y: number }>): JSX.Element {
  const { x, y } = props;
  switch (props.glyph) {
    case 'bug':
      return <g>
        <path d={`M${x - 7} ${y - 4}l-4 -3M${x + 7} ${y - 4}l4 -3M${x - 8} ${y + 1}l-5 0M${x + 8} ${y + 1}l5 0M${x - 7} ${y + 5}l-4 3M${x + 7} ${y + 5}l4 3`} stroke={INK} strokeWidth={1.6} strokeLinecap="round" />
        <ellipse cx={x} cy={y + 1} rx={7.5} ry={8.5} fill={TINTS.coral.base} stroke={INK} strokeWidth={1.6} />
        <path d={`M${x} ${y - 7}L${x} ${y + 9}`} stroke={INK} strokeWidth={1.2} />
      </g>;
    case 'star':
      return <path d={closedPath(Array.from({ length: 10 }, (_, i) => {
        const r = i % 2 === 0 ? 10 : 4.4;
        const a = -Math.PI / 2 + (i * Math.PI) / 5;
        return [x + Math.cos(a) * r, y + 1 + Math.sin(a) * r] as const;
      }))} fill={TINTS.peach.base} stroke={INK} strokeWidth={1.6} strokeLinejoin="round" />;
    case 'question':
      return <text x={x} y={y + 8} textAnchor="middle" fontSize={22} fontWeight={800} fontFamily="var(--display)" fill={INK}>?</text>;
    case 'heart':
      return <path d={`M${x} ${y + 9}C${x - 13} ${y} ${x - 9} ${y - 10} ${x} ${y - 4}C${x + 9} ${y - 10} ${x + 13} ${y} ${x} ${y + 9}Z`} fill={TINTS.coral.base} stroke={INK} strokeWidth={1.6} strokeLinejoin="round" />;
    case 'alert':
      return <g>
        <path d={`M${x} ${y - 11}L${x + 11.5} ${y + 9}L${x - 11.5} ${y + 9}Z`} fill={TINTS.coral.base} stroke={INK} strokeWidth={1.8} strokeLinejoin="round" />
        <path d={`M${x} ${y - 4}L${x} ${y + 2.5}`} stroke={INK} strokeWidth={2.6} strokeLinecap="round" />
        <circle cx={x} cy={y + 6} r={1.5} fill={INK} />
      </g>;
    case 'lines':
      return <path d={`M${x - 11} ${y - 5}L${x + 11} ${y - 5}M${x - 11} ${y + 1}L${x + 7} ${y + 1}M${x - 11} ${y + 7}L${x + 2} ${y + 7}`} stroke={INK} strokeWidth={2.4} strokeLinecap="round" />;
  }
}

/** A speech balloon with a tail down to its ground point; each cue animates in its own way. */
export function SpeechBubble(props: Readonly<{ bubble: Bubble; index: number; shown: boolean }>): JSX.Element {
  const [px, py] = iso(props.bubble.at);
  const w = 42;
  const h = 34;
  const cx = px;
  const cy = py - h / 2 - 12;
  const body = `M${cx - w / 2 + 9} ${cy - h / 2}L${cx + w / 2 - 9} ${cy - h / 2}Q${cx + w / 2} ${cy - h / 2} ${cx + w / 2} ${cy - h / 2 + 9}L${cx + w / 2} ${cy + h / 2 - 9}Q${cx + w / 2} ${cy + h / 2} ${cx + w / 2 - 9} ${cy + h / 2}L${cx + 5} ${cy + h / 2}L${px - 1} ${py}L${cx - 6} ${cy + h / 2}L${cx - w / 2 + 9} ${cy + h / 2}Q${cx - w / 2} ${cy + h / 2} ${cx - w / 2} ${cy + h / 2 - 9}L${cx - w / 2} ${cy - h / 2 + 9}Q${cx - w / 2} ${cy - h / 2} ${cx - w / 2 + 9} ${cy - h / 2}Z`;
  const cue = props.bubble.cue;
  return <g className={`city-bubble city-bubble-${cue}` + (props.shown ? ' is-shown' : '')} style={{ animationDelay: cue === 'feed' ? `${props.index * -1.5}s` : undefined, transformOrigin: `${px.toFixed(1)}px ${py.toFixed(1)}px` }}>
    <path d={body} transform="translate(3 3)" fill={INK} />
    <path d={body} fill="#ffffff" stroke={INK} strokeWidth={2} strokeLinejoin="round" />
    <Glyph glyph={props.bubble.glyph} x={cx} y={cy - 1} />
  </g>;
}

/**
 * What travels through the city: first the Case File, a coral folder with a white sheet; after the
 * site, the build it became (`BuildToken`). Each bobs a little above one shared ground shadow. All
 * are tiny SVGs drawn about their origin that the clock moves (`data-mover`), so the browser only
 * recomposites small layers. The shadow lies on the ground, so the city paints it under every item
 * and rig; the tokens float over them.
 */
export function CaseShadow(): JSX.Element {
  return <svg data-mover="case-shadow" className="city-token" width={1} height={1}><ellipse cx={0} cy={0} rx={17} ry={8.5} fill="rgba(17,17,17,0.16)" /></svg>;
}

export function CaseToken(): JSX.Element {
  return <svg data-mover="case" className="city-token" width={1} height={1}>
    <g className="city-case-bob" transform="translate(0 -18) scale(1.35)">
      <path d="M-15 -8L-15 9Q-15 11 -13 11L13 11Q15 11 15 9L15 -6Q15 -8 13 -8L-1 -8L-4 -12L-13 -12Q-15 -12 -15 -10Z" fill={CORAL_STRONG} stroke={INK} strokeWidth={2} strokeLinejoin="round" />
      <path d="M-10 -9L10 -9L10 6L-10 6Z" fill="#ffffff" stroke={INK} strokeWidth={1.4} strokeLinejoin="round" />
      <path d="M-6 -5L5 -5M-6 -1L3 -1" stroke={INK} strokeWidth={1.4} strokeLinecap="round" />
      <path d="M-15 -3L15 -3L15 9Q15 11 13 11L-13 11Q-15 11 -15 9Z" fill={TINTS.coral.base} stroke={INK} strokeWidth={2} strokeLinejoin="round" />
    </g>
  </svg>;
}

/** The build black hands to mint: a small app window, mint title bar, rising bars, and a spark for new. */
export function BuildToken(): JSX.Element {
  return <svg data-mover="build" className="city-token" width={1} height={1} opacity={0}>
    <g className="city-case-bob" transform="translate(0 -18) scale(1.35)">
      <path d="M-13 -11L13 -11Q15 -11 15 -9L15 9Q15 11 13 11L-13 11Q-15 11 -15 9L-15 -9Q-15 -11 -13 -11Z" fill="#ffffff" stroke={INK} strokeWidth={2} strokeLinejoin="round" />
      <path d="M-15 -4.5L15 -4.5L15 -9Q15 -11 13 -11L-13 -11Q-15 -11 -15 -9Z" fill={TINTS.mint.base} stroke={INK} strokeWidth={2} strokeLinejoin="round" />
      {[-10.5, -7.5, -4.5].map((x) => <circle key={x} cx={x} cy={-7.7} r={1} fill={INK} />)}
      <path d="M-10 7L10 7" stroke={INK} strokeWidth={1.4} strokeLinecap="round" />
      <path d="M-8.5 7L-8.5 3.5L-4.5 3.5L-4.5 7Z" fill={TINTS.sky.base} stroke={INK} strokeWidth={1.2} strokeLinejoin="round" />
      <path d="M-2 7L-2 1L2 1L2 7Z" fill={TINTS.mint.base} stroke={INK} strokeWidth={1.2} strokeLinejoin="round" />
      <path d="M4.5 7L4.5 -1.5L8.5 -1.5L8.5 7Z" fill={CORAL_STRONG} stroke={INK} strokeWidth={1.2} strokeLinejoin="round" />
      <path d="M17 -17L18.3 -13.7L21.6 -12.4L18.3 -11.1L17 -7.8L15.7 -11.1L12.4 -12.4L15.7 -13.7Z" fill={TINTS.peach.base} stroke={INK} strokeWidth={1.2} strokeLinejoin="round" />
    </g>
  </svg>;
}
