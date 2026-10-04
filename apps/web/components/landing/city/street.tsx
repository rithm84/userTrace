import type { JSX } from 'react';

import { Solid } from './buildings';
import { closedPath, faceRect, iso, rightFace, type Box, type Pt } from './iso';
import { INK, TINTS, W } from './paint';
import { GroundShadow } from './props';

function centre(b: Box): readonly [number, number] {
  return [b.x + b.w / 2, b.y + b.d / 2];
}

/** A street lamp: plinth, tapered post, lantern with a warm glass and a cap. */
export function Lamp(props: Readonly<{ box: Box }>): JSX.Element {
  const [x, y] = centre(props.box);
  const foot = iso([x, y, 0]);
  const [, top] = iso([x, y, 3.55]);
  const lantern: Box = { x: x - 0.24, y: y - 0.24, z: 3.55, w: 0.48, d: 0.48, h: 0.62 };
  return <g>
    <GroundShadow at={foot} rx={9} />
    <Solid box={{ x: x - 0.3, y: y - 0.3, z: 0, w: 0.6, d: 0.6, h: 0.35 }} tint="grey" weight={W.fine} />
    <path d={closedPath([[foot[0] - 2.4, foot[1] - 7], [foot[0] - 1.5, top], [foot[0] + 1.5, top], [foot[0] + 2.4, foot[1] - 7]])} fill="#2a2a2a" stroke={INK} strokeWidth={W.hair} />
    <Solid box={lantern} tint="peach" weight={W.fine} />
    <path d={faceRect(rightFace(lantern), 0.1, 0.12, 0.28, 0.38)} fill="#fff4e6" stroke={INK} strokeWidth={W.hair} />
    <Solid box={{ x: x - 0.34, y: y - 0.34, z: 4.17, w: 0.68, d: 0.68, h: 0.12 }} tint="grey" weight={W.fine} />
    <circle cx={iso([x, y, 4.42])[0]} cy={iso([x, y, 4.42])[1]} r={2} fill={INK} />
  </g>;
}

/** A slatted park bench along one axis. */
export function Bench(props: Readonly<{ box: Box; along: 'x' | 'y' }>): JSX.Element {
  const b = props.box;
  const x = props.along === 'x';
  const slat = (offset: number, z: number, depth: number, h: number): Box => (x ? { x: b.x, y: b.y + offset, z, w: b.w, d: depth, h } : { x: b.x + offset, y: b.y, z, w: depth, d: b.d, h });
  const legs: readonly Pt[] = x
    ? [iso([b.x + 0.25, b.y + b.d - 0.1, 0]), iso([b.x + b.w - 0.25, b.y + b.d - 0.1, 0]), iso([b.x + 0.25, b.y + 0.15, 0]), iso([b.x + b.w - 0.25, b.y + 0.15, 0])]
    : [iso([b.x + b.w - 0.1, b.y + 0.25, 0]), iso([b.x + b.w - 0.1, b.y + b.d - 0.25, 0]), iso([b.x + 0.15, b.y + 0.25, 0]), iso([b.x + 0.15, b.y + b.d - 0.25, 0])];
  const depth = x ? b.d : b.w;
  return <g>
    <GroundShadow at={iso([b.x + b.w / 2, b.y + b.d / 2, 0])} rx={24} />
    {legs.slice(2).map(([lx, ly], i) => <path key={`b${i}`} d={`M${lx} ${ly}L${lx} ${ly - 36}`} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />)}
    <Solid box={slat(0, 1.25, 0.16, 0.28)} tint="peach" weight={W.hair} />
    <Solid box={slat(0, 1.7, 0.16, 0.28)} tint="peach" weight={W.hair} />
    {[0.14, 0.4, 0.66].map((t) => <Solid key={t} box={slat(depth * t, 0.88, depth * 0.22, 0.14)} tint="peach" weight={W.hair} />)}
    {legs.slice(0, 2).map(([lx, ly], i) => <path key={`f${i}`} d={`M${lx} ${ly}L${lx} ${ly - 18}`} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />)}
  </g>;
}

/** A traffic cone: a dark square base plate, and a coral cone with a round foot, a white band and a shaded right side. */
function Cone(props: Readonly<{ box: Box }>): JSX.Element {
  const [x, y] = centre(props.box);
  const half = 0.33;
  const plateH = 0.12;
  const corner = (dx: number, dy: number, z: number) => iso([x + dx * half, y + dy * half, z]);
  const [cx, cy] = iso([x, y, plateH]);
  const height = 21;
  const foot = 5.2;
  const tip = 1.1;
  // the cone's radius and the iso ellipse's half-height at h pixels above its foot
  const radius = (h: number): number => foot - ((foot - tip) * h) / height;
  const ring = (h: number, sweep: 0 | 1, from: -1 | 1): string => `A${radius(h).toFixed(2)} ${(radius(h) / 1.732).toFixed(2)} 0 0 ${sweep} ${(cx - from * radius(h)).toFixed(2)} ${(cy - h).toFixed(2)}`;
  const body = `M${cx - foot} ${cy}L${cx - tip} ${cy - height}L${cx + tip} ${cy - height}L${cx + foot} ${cy}${ring(0, 1, 1)}Z`;
  const [b0, b1] = [8, 13.5];
  const band = `M${cx - radius(b0)} ${cy - b0}${ring(b0, 0, -1)}L${cx + radius(b1)} ${cy - b1}${ring(b1, 1, 1)}Z`;
  const shade = `M${cx} ${cy + foot / 1.732}L${cx + tip * 0.3} ${cy - height}L${cx + tip} ${cy - height}L${cx + foot} ${cy}A${foot} ${(foot / 1.732).toFixed(2)} 0 0 1 ${cx} ${(cy + foot / 1.732).toFixed(2)}Z`;
  return <g>
    <path d={closedPath([corner(-1, 1, 0), corner(1, 1, 0), corner(1, 1, plateH), corner(-1, 1, plateH)])} fill="#2c2c2c" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
    <path d={closedPath([corner(1, 1, 0), corner(1, -1, 0), corner(1, -1, plateH), corner(1, 1, plateH)])} fill="#1c1c1c" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
    <path d={closedPath([corner(-1, -1, plateH), corner(1, -1, plateH), corner(1, 1, plateH), corner(-1, 1, plateH)])} fill="#4a4a4a" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
    <path d={body} fill={TINTS.coral.base} />
    <path d={band} fill="#ffffff" />
    <path d={shade} fill="rgba(17,17,17,0.14)" />
    <path d={body} fill="none" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
  </g>;
}

/** A small round café table with two cups. */
export function Table(props: Readonly<{ box: Box }>): JSX.Element {
  const [x, y] = centre(props.box);
  const [cx, cy] = iso([x, y, 0]);
  // true-isometric circles (height = width / 1.732): a round base plate, a post, a top with a rim
  const top = cy - 26;
  const [rx, ry] = [18, 18 / 1.732];
  // flat on the top, a phone lies along the table's axes: one step along gx and one along gy
  const along: Pt = [7.8, 4.5];
  const across: Pt = [-4.3, 2.5];
  const phone: Pt = [cx + 1.5, top - 3.2];
  const corner = (a: number, b: number): Pt => [phone[0] + along[0] * a + across[0] * b, phone[1] + along[1] * a + across[1] * b];
  return <g>
    <GroundShadow at={[cx, cy]} rx={13} />
    <ellipse cx={cx} cy={cy} rx={7} ry={7 / 1.732} fill="#d9dade" stroke={INK} strokeWidth={W.fine} />
    <path d={`M${cx} ${cy - 1}L${cx} ${top + 2}`} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />
    <path d={`M${cx - rx} ${top}L${cx - rx} ${top + 2.6}A${rx} ${ry} 0 0 0 ${cx + rx} ${top + 2.6}L${cx + rx} ${top}Z`} fill="#d9dade" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <ellipse cx={cx} cy={top} rx={rx} ry={ry} fill="#ffffff" stroke={INK} strokeWidth={W.line} />
    <path d={closedPath([corner(0, 0), corner(1, 0), corner(1, 1), corner(0, 1)])} fill={TINTS.lilac.base} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={closedPath([corner(0.18, 0.22), corner(0.82, 0.22), corner(0.82, 0.78), corner(0.18, 0.78)])} fill="#ffffff" />
    <ellipse cx={cx - 7} cy={top + 0.5} rx={4.4} ry={4.4 / 1.732} fill="#ffffff" stroke={INK} strokeWidth={W.hair} />
    <path d={`M${cx - 10} ${top}L${cx - 10} ${top - 5.5}A3 1.73 0 0 0 ${cx - 4} ${top - 5.5}L${cx - 4} ${top}A3 1.73 0 0 1 ${cx - 10} ${top}Z`} fill="#ffffff" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={`M${cx - 10} ${top - 3.4}L${cx - 4} ${top - 3.4}L${cx - 4} ${top - 1.8}A3 1.73 0 0 1 ${cx - 10} ${top - 1.8}Z`} fill={TINTS.coral.base} />
    <ellipse cx={cx - 7} cy={top - 5.5} rx={3} ry={1.73} fill="#8a5a3c" stroke={INK} strokeWidth={W.hair} />
    <path d={`M${cx - 4} ${top - 4.4}q2.8 0.4 0 3`} fill="none" stroke={INK} strokeWidth={W.fine} strokeLinecap="round" />
  </g>;
}

/** Street works: an open manhole with its cover set aside and two cones behind it. Aqua climbs out of it. */
export function Dig(props: Readonly<{ box: Box }>): JSX.Element {
  const [x, y] = centre(props.box);
  const [cx, cy] = iso([x, y, 0]);
  const rx = 30;
  const ry = rx / 1.732;
  const wall = `M${cx - rx} ${cy}A${rx} ${ry} 0 0 1 ${cx + rx} ${cy}L${cx + rx - 3} ${cy + 7}A${rx - 3} ${ry - 2} 0 0 0 ${cx - rx + 3} ${cy + 7}Z`;
  const cone = (dx: number, dy: number): Box => ({ x: x + dx - 0.3, y: y + dy - 0.3, z: 0, w: 0.6, d: 0.6, h: 1 });
  return <g>
    <Cone box={cone(-1.9, -1.2)} />
    <Cone box={cone(-1.2, -1.9)} />
    <ellipse cx={cx} cy={cy} rx={rx + 6} ry={ry + 3.5} fill="#d6d8dd" stroke={INK} strokeWidth={W.hair} />
    <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="#262626" stroke={INK} strokeWidth={W.fine} />
    <path d={wall} fill="#4b4b4b" />
    {[0, 1, 2].map((i) => <path key={i} d={`M${cx - 9} ${cy - 2 + i * 7}L${cx + 9} ${cy - 2 + i * 7}`} stroke="#8a8a8a" strokeWidth={1.6} strokeLinecap="round" />)}
    <g transform={`translate(${(cx + rx + 14).toFixed(1)} ${(cy - 12).toFixed(1)}) rotate(-12)`}>
      <ellipse cx={0} cy={0} rx={9} ry={17} fill="#bfc2c9" stroke={INK} strokeWidth={W.fine} />
      <ellipse cx={-2} cy={0} rx={5.5} ry={12} fill="none" stroke={INK} strokeWidth={W.hair} />
      <path d="M-6 -6L2 6M-6 4L1 -8" stroke={INK} strokeWidth={W.hair} opacity={0.6} />
    </g>
  </g>;
}
