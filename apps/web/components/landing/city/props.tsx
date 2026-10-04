import type { JSX } from 'react';

import { Solid } from './buildings';
import { closedPath, faceLine, faceRect, iso, leftFace, openPath, rightFace, topFace, type Box, type Face, type Pt } from './iso';
import { PLANTS, SpriteImage, type Plant } from './sprites';
import { GLASS, INK, TINTS, W } from './paint';

const SHADOW = 'rgba(17, 17, 17, 0.12)';

function centre(b: Box): Pt {
  return iso([b.x + b.w / 2, b.y + b.d / 2, b.z]);
}

/** A soft contact shadow on the ground, so standing things sit on it. */
export function GroundShadow(props: Readonly<{ at: Pt; rx: number }>): JSX.Element {
  return <ellipse cx={props.at[0]} cy={props.at[1]} rx={props.rx} ry={props.rx * 0.5} fill={SHADOW} />;
}

/** A street plant: the traced drawing on its trunk base, swaying gently about it. */
export function Tree(props: Readonly<{ box: Box; plant: Plant; size: number; flip: boolean; sway: boolean }>): JSX.Element {
  const at = centre(props.box);
  const plant = { ...PLANTS[props.plant], scale: PLANTS[props.plant].scale * props.size };
  const spread = plant.width * plant.scale * 0.34;
  return <g>
    <GroundShadow at={at} rx={Math.max(6, spread)} />
    <g className={props.sway ? 'city-sway' : undefined} style={props.sway ? { transformOrigin: `${at[0].toFixed(1)}px ${at[1].toFixed(1)}px`, animationDelay: `${(-(at[0] * 7) % 3800).toFixed(0)}ms` } : undefined}>
      <SpriteImage sprite={plant} at={at} flip={props.flip} />
    </g>
  </g>;
}

/** A raised planter box with shrubs along its length. */
export function Planter(props: Readonly<{ box: Box }>): JSX.Element {
  const b = props.box;
  const along = b.w >= b.d;
  const count = Math.max(1, Math.round((along ? b.w : b.d) / 1.3));
  return <g>
    <Solid box={b} tint="white" weight={W.line} />
    <path d={faceRect(topFace(b), 0.15, 0.15, b.w - 0.3, b.d - 0.3)} fill="#8f7a64" />
    {Array.from({ length: count }, (_, i) => {
      const t = (i + 0.5) / count;
      const at = iso(along ? [b.x + b.w * t, b.y + b.d / 2, b.h] : [b.x + b.w / 2, b.y + b.d * t, b.h]);
      return <SpriteImage key={i} sprite={PLANTS[i % 2 === 0 ? 'shrub' : 'bush']} at={[at[0], at[1] + 3]} flip={i % 3 === 1} />;
    })}
  </g>;
}

/** An app panel fitted into a bay of the frame: white, with the coral title bar of the panel the crane carries. */
function FittedPanel(props: Readonly<{ face: Face; u: number; v: number; w: number; h: number }>): JSX.Element {
  const { face, u, v, w, h } = props;
  const bar = Math.min(0.3, h * 0.24);
  return <g>
    <path d={faceRect(face, u, v, w, h)} fill="#ffffff" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={faceRect(face, u, v + h - bar, w, bar)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
    <path d={faceLine(face, [[u + 0.22, v + h - bar - 0.26], [u + w * 0.62, v + h - bar - 0.26]])} stroke={INK} strokeWidth={W.fine} strokeLinecap="round" />
    <path d={faceLine(face, [[u + 0.22, v + h - bar - 0.52], [u + w * 0.8, v + h - bar - 0.52]])} stroke="#c9ccd3" strokeWidth={W.fine} strokeLinecap="round" />
  </g>;
}

/**
 * The building going up: a two-storey concrete frame on a base slab. The ground floor is glazed;
 * upstairs, app panels stand in the bays between the columns and the right front bay (clear of
 * black, who works at the left) is still open, waiting for the panel the crane lowers in front of it.
 */
export function Frame(props: Readonly<{ box: Box }>): JSX.Element {
  const b = props.box;
  const L = leftFace(b);
  const R = rightFace(b);
  const base = 0.25;
  const slab = 0.22;
  const storey = (b.h - base) / 2;
  const mid = base + storey;
  const col = 0.3;
  const bay = (width: number, k: number): readonly [number, number] => [k * (width / 2) + col / 2, width / 2 - col];
  const glass = (face: Face) => <g>
    <path d={faceRect(face, 0, base, face.width, storey)} fill={GLASS} />
    {[1, 3].map((k) => <path key={k} d={faceLine(face, [[(face.width * k) / 4, base], [(face.width * k) / 4, mid]])} stroke={INK} strokeWidth={W.hair} />)}
    <path d={faceLine(face, [[0, base + storey * 0.62], [face.width, base + storey * 0.62]])} stroke={INK} strokeWidth={W.hair} />
  </g>;
  const open = (face: Face, k: number, shade: string) => {
    const [u, w] = bay(face.width, k);
    return <path d={faceRect(face, u - col / 2, mid, w + col, b.h - slab - mid)} fill={shade} />;
  };
  const fitted = (face: Face, k: number) => {
    const [u, w] = bay(face.width, k);
    return <FittedPanel face={face} u={u + 0.08} v={mid + slab / 2 + 0.1} w={w - 0.16} h={b.h - slab - mid - slab / 2 - 0.2} />;
  };
  const column = (x: number, y: number) => <Solid key={`${x}-${y}`} box={{ x, y, z: base, w: col, d: col, h: b.h - slab - base }} tint="grey" weight={W.fine} />;
  const band = (face: Face, v: number, h: number) => <path d={faceRect(face, 0, v, face.width, h)} fill="#eceef1" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />;
  return <g>
    <Solid box={{ x: b.x - 0.2, y: b.y - 0.2, z: 0, w: b.w + 0.4, d: b.d + 0.4, h: base }} tint="grey" weight={W.line} />
    {glass(L)}
    {glass(R)}
    {fitted(L, 0)}
    {open(L, 1, '#aeb3bb')}
    {fitted(R, 0)}
    {open(R, 1, '#9ea3ab')}
    {band(L, mid - slab / 2, slab)}
    {band(R, mid - slab / 2, slab)}
    {column(b.x, b.y + b.d - col)}
    {column(b.x + b.w / 2 - col / 2, b.y + b.d - col)}
    {column(b.x + b.w - col, b.y)}
    {column(b.x + b.w - col, b.y + b.d / 2 - col / 2)}
    {column(b.x + b.w - col, b.y + b.d - col)}
    <Solid box={{ x: b.x, y: b.y, z: b.h - slab, w: b.w, d: b.d, h: slab }} tint="grey" weight={W.line} />
  </g>;
}

/** The counter-jib's length behind the mast and the cab's height, in ground units. */
export const CRANE = Object.freeze({ back: 2.6, cab: 1.4 });

/** A lattice strut from one screen point to another: a peach band with a zigzag of bracing. */
function lattice(from: Pt, to: Pt, width: number, steps: number): JSX.Element {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const length = Math.hypot(dx, dy);
  const nx = (-dy / length) * width;
  const ny = (dx / length) * width;
  const zig: Pt[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const side = i % 2 === 0 ? 0.5 : -0.5;
    zig.push([from[0] + dx * t + nx * side, from[1] + dy * t + ny * side]);
  }
  return <g>
    <path d={closedPath([[from[0] + nx / 2, from[1] + ny / 2], [to[0] + nx / 2, to[1] + ny / 2], [to[0] - nx / 2, to[1] - ny / 2], [from[0] - nx / 2, from[1] - ny / 2]])} fill={TINTS.peach.base} stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <path d={openPath(zig)} fill="none" stroke={INK} strokeWidth={W.hair} />
  </g>;
}

/**
 * A flat-top tower crane in two parts, so the city can sort them apart: the mast on its footing
 * (`box` is the mast), and the head up top, whose jib reaches `reach` units along +gx over
 * whatever stands lower. The hook is drawn by the choreography.
 */
export function CraneMast(props: Readonly<{ box: Box }>): JSX.Element {
  const b = props.box;
  const mx = b.x + b.w / 2;
  const my = b.y + b.d / 2;
  return <g>
    <Solid box={{ x: mx - 1.2, y: my - 1.2, z: 0, w: 2.4, d: 2.4, h: 0.6 }} tint="grey" weight={W.line} />
    {lattice(iso([mx, my, 0]), iso([mx, my, b.h]), 11, Math.round(b.h * 1.2))}
  </g>;
}

/** The box the crane's head (counterweight, cab, jib) occupies above its mast. */
export function craneHead(mast: Box, reach: number): Box {
  const mx = mast.x + mast.w / 2;
  const my = mast.y + mast.d / 2;
  return { x: mx - CRANE.back, y: my - 0.7, z: mast.h - 0.7, w: CRANE.back + reach, d: 1.4, h: CRANE.cab + 0.5 };
}

export function CraneHead(props: Readonly<{ box: Box; reach: number }>): JSX.Element {
  const b = props.box;
  const mx = b.x + b.w / 2;
  const my = b.y + b.d / 2;
  const top = b.h;
  const cab: Box = { x: mx - 0.7, y: my - 0.7, z: top - 0.2, w: 1.4, d: 1.4, h: CRANE.cab };
  return <g>
    {lattice(iso([mx - CRANE.back + 0.5, my, top + 0.3]), iso([mx, my, top + 0.3]), 8, 3)}
    {/* the counterweight stands taller than the counter-jib, so the jib's end sits inside it */}
    <Solid box={{ x: mx - CRANE.back - 0.1, y: my - 0.55, z: top - 0.6, w: 1.2, d: 1.1, h: 1.2 }} tint="grey" weight={W.line} />
    <Solid box={cab} tint="peach" weight={W.line} />
    <path d={faceRect(rightFace(cab), 0.25, 0.4, 0.9, 0.7)} fill="#dcf0fd" stroke={INK} strokeWidth={W.hair} />
    {lattice(iso([mx + 0.7, my, top + 0.3]), iso([mx + props.reach, my, top + 0.3]), 8, Math.round(props.reach * 1.3))}
  </g>;
}

/** The panel the crane carries: a small white app window with a coral title bar, on its way to the frame. */
export function CraneLoad(props: Readonly<{ box: Box }>): JSX.Element {
  const b = props.box;
  const face = leftFace(b);
  const bar = 0.34;
  return <g>
    <Solid box={b} tint="white" weight={W.line} />
    <path d={faceRect(face, 0, face.height - bar, face.width, bar)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={faceLine(face, [[0.3, face.height - bar - 0.28], [face.width * 0.62, face.height - bar - 0.28]])} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />
    <path d={faceLine(face, [[0.3, face.height - bar - 0.58], [face.width * 0.8, face.height - bar - 0.58]])} stroke="#c9ccd3" strokeWidth={W.fine} strokeLinecap="round" />
  </g>;
}
