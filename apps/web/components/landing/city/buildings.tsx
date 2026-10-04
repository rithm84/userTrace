import type { JSX } from 'react';

import { FacadeArt } from './facades';
import { faceEllipse, faceLine, faceRect, groundRect, iso, latticeTile, leftFace, rightFace, topFace, type Box } from './iso';
import type { Facade, Roof } from './model';
import { INK, mix, TINTS, W, type Tint } from './paint';

const HATCH_ID = 'city-hatch';
export const PAVING_ID = 'city-paving';
export const ASPHALT_ID = 'city-asphalt';

/**
 * The shared fills, defined once in the first plane and used by every later one: hatching for shaded
 * faces, a paving grid that follows the isometric ground (1.5-unit slabs on the sidewalk tops), and a
 * speckle for asphalt.
 */
export function CityDefs(): JSX.Element {
  const pave = latticeTile(1.5, 0.3);
  return <defs>
    <pattern id={HATCH_ID} patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(58)">
      <line x1="0" y1="0" x2="0" y2="5" stroke={INK} strokeWidth="0.85" />
    </pattern>
    <pattern id={PAVING_ID} patternUnits="userSpaceOnUse" x={pave.x} y={pave.y} width={pave.width} height={pave.height}>
      <path d={`M0 ${pave.height / 2}L${pave.width / 2} 0L${pave.width} ${pave.height / 2}L${pave.width / 2} ${pave.height}Z`} fill="none" stroke="#e4e5e9" strokeWidth="1" />
    </pattern>
    <pattern id={ASPHALT_ID} patternUnits="userSpaceOnUse" width="46" height="27">
      {[[5, 4, 0.9], [19, 11, 0.7], [33, 6, 1], [41, 19, 0.8], [11, 21, 0.9], [27, 24, 0.6]].map(([x, y, r]) => <circle key={`${x}-${y}`} cx={x} cy={y} r={r} fill="#c9ccd3" />)}
    </pattern>
  </defs>;
}

/** A plain shaded box: lit left face, hatched right face, pale top, ink edges. */
export function Solid(props: Readonly<{ box: Box; tint: Tint; weight: number }>): JSX.Element {
  const b = props.box;
  const t = TINTS[props.tint];
  const L = leftFace(b);
  const R = rightFace(b);
  const T = topFace(b);
  return <g>
    <path d={faceRect(L, 0, 0, L.width, L.height)} fill={t.left} stroke={INK} strokeWidth={props.weight} strokeLinejoin="round" />
    <path d={faceRect(R, 0, 0, R.width, R.height)} fill={t.right} stroke={INK} strokeWidth={props.weight} strokeLinejoin="round" />
    <path d={faceRect(R, 0, 0, R.width, R.height)} fill={`url(#${HATCH_ID})`} opacity={0.22} />
    <path d={faceRect(T, 0, 0, T.width, T.height)} fill={t.top} stroke={INK} strokeWidth={props.weight} strokeLinejoin="round" />
  </g>;
}

/** How far the roof sits below the parapet, and how wide the parapet is, in ground units. */
const PARAPET = Object.freeze({ inset: 0.34, drop: 0.34 });

/** A building: two painted facades over a darker plinth, a recessed roof behind a parapet, roof furniture, ink edges over everything. */
export function Building(props: Readonly<{ id: string; box: Box; tint: Tint; left: Facade; right: Facade; roof: Roof; seed: number }>): JSX.Element {
  const b = props.box;
  const t = TINTS[props.tint];
  const L = leftFace(b);
  const R = rightFace(b);
  const T = topFace(b);
  const edge = { stroke: INK, strokeWidth: W.heavy, strokeLinejoin: 'round' as const, fill: 'none' };
  return <g>
    <g>
      <path d={faceRect(L, 0, 0, L.width, L.height)} fill={t.left} />
      <path d={faceRect(L, 0, 0, L.width, 0.42)} fill={mix(t.left, '#5a5a5a', 0.16)} />
      <path d={faceLine(L, [[0, 0.42], [L.width, 0.42]])} stroke={INK} strokeWidth={W.hair} />
      <path d={faceLine(L, [[0, b.h - 0.5], [L.width, b.h - 0.5]])} stroke={INK} strokeWidth={W.hair} opacity={0.55} />
      {/* the outline goes under the facade art, so anything that projects from the wall (an awning) covers it */}
      <path d={faceRect(L, 0, 0, L.width, L.height)} {...edge} />
      <FacadeArt face={L} facade={props.left} id={`${props.id}-l`} seed={props.seed} />
    </g>
    <g>
      <path d={faceRect(R, 0, 0, R.width, R.height)} fill={t.right} />
      <path d={faceRect(R, 0, 0, R.width, R.height)} fill={`url(#${HATCH_ID})`} opacity={0.2} />
      <path d={faceRect(R, 0, 0, R.width, 0.42)} fill={mix(t.right, '#5a5a5a', 0.2)} />
      <path d={faceLine(R, [[0, 0.42], [R.width, 0.42]])} stroke={INK} strokeWidth={W.hair} />
      <path d={faceLine(R, [[0, b.h - 0.5], [R.width, b.h - 0.5]])} stroke={INK} strokeWidth={W.hair} opacity={0.55} />
      <path d={faceRect(R, 0, 0, R.width, R.height)} {...edge} />
      <FacadeArt face={R} facade={props.right} id={`${props.id}-r`} seed={props.seed + 101} />
    </g>
    <g>
      <path d={faceRect(T, 0, 0, T.width, T.height)} fill={t.top} stroke={INK} strokeWidth={W.heavy} strokeLinejoin="round" />
      <Recess id={props.id} box={b} tint={props.tint} />
      <RoofFurniture box={b} roof={props.roof} />
    </g>
  </g>;
}

/** The roof sunk behind its parapet: the two far inner walls show as a band above the lowered deck. */
function Recess(props: Readonly<{ id: string; box: Box; tint: Tint }>): JSX.Element {
  const b = props.box;
  const t = TINTS[props.tint];
  const i = PARAPET.inset;
  const rim = groundRect(b.x + i, b.y + i, b.w - 2 * i, b.d - 2 * i, b.z + b.h);
  const clipId = `city-roof-${props.id}`;
  return <g>
    <clipPath id={clipId}><path d={rim} /></clipPath>
    <path d={rim} fill={mix(t.right, '#6b6b6b', 0.1)} />
    <path d={groundRect(b.x + i, b.y + i, b.w - 2 * i, b.d - 2 * i, b.z + b.h - PARAPET.drop)} fill={mix(t.top, '#8a8a8a', 0.1)} clipPath={`url(#${clipId})`} />
    <path d={rim} fill="none" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
  </g>;
}

function RoofFurniture(props: Readonly<{ box: Box; roof: Roof }>): JSX.Element | null {
  const b = props.box;
  const top = b.z + b.h - PARAPET.drop;
  switch (props.roof) {
    case 'plain':
      return null;
    case 'units': {
      // one unit in the back corner, and a second along whichever back edge has room for it with a
      // gap at least as wide as a unit, so on screen neither unit ever covers the other
      const unit = { w: 1.2, d: 1.1 };
      const inset = 0.75;
      const units: { x: number; y: number; w: number; d: number }[] = [{ x: b.x + inset, y: b.y + inset, ...unit }];
      if (b.w >= 2 * inset + 2 * unit.w + unit.d + 0.2) units.push({ x: b.x + b.w - inset - unit.w, y: b.y + inset, ...unit });
      else if (b.d >= 2 * inset + 2 * unit.d + unit.w + 0.2) units.push({ x: b.x + inset, y: b.y + b.d - inset - unit.d, ...unit });
      return <g>{units.map((u, i) => {
        const box: Box = { x: u.x, y: u.y, z: top, w: u.w, d: u.d, h: 0.8 };
        const T = topFace(box);
        const R = rightFace(box);
        const L = leftFace(box);
        return <g key={i}>
          <Solid box={box} tint="grey" weight={W.fine} />
          {[0.2, 0.4, 0.6, 0.8].map((k) => <path key={k} d={faceLine(L, [[L.width * k, 0.15], [L.width * k, 0.65]])} stroke={INK} strokeWidth={W.hair} opacity={0.6} />)}
          <path d={faceRect(R, 0.2, 0.2, R.width - 0.4, 0.4)} fill="#ffffff" stroke={INK} strokeWidth={W.hair} />
          <path d={faceEllipse(T, u.w / 2, u.d / 2, Math.min(u.w, u.d) * 0.32, Math.min(u.w, u.d) * 0.32)} fill="#ffffff" stroke={INK} strokeWidth={W.hair} />
          <path d={faceLine(T, [[u.w / 2 - Math.min(u.w, u.d) * 0.3, u.d / 2], [u.w / 2 + Math.min(u.w, u.d) * 0.3, u.d / 2]])} stroke={INK} strokeWidth={W.hair} />
        </g>;
      })}</g>;
    }
    case 'tank':
      return <WaterTank at={iso([b.x + b.w * 0.6, b.y + b.d * 0.45, top])} />;
  }
}

/**
 * A rooftop water tank on braced legs: a banded cylinder under a true cone, both shaded on their
 * right half, with a ladder up the lit side. Drawn in screen space with isometric ellipses (a
 * ground circle's width is 1.732 times its height).
 */
function WaterTank(props: Readonly<{ at: readonly [number, number] }>): JSX.Element {
  const [cx, cy] = props.at;
  const rx = 16;
  const ry = rx / 1.732;
  const legH = 13;
  const bodyH = 26;
  const coneH = 13;
  const bottom = cy - legH;
  const top = bottom - bodyH;
  const legs = [[cx - rx * 0.78, cy], [cx, cy + ry * 0.78], [cx + rx * 0.78, cy]] as const;
  const body = `M${cx - rx} ${top}L${cx - rx} ${bottom}A${rx} ${ry} 0 0 0 ${cx + rx} ${bottom}L${cx + rx} ${top}A${rx} ${ry} 0 0 0 ${cx - rx} ${top}Z`;
  const bodyShade = `M${cx} ${top + ry}L${cx} ${bottom + ry}A${rx} ${ry} 0 0 0 ${cx + rx} ${bottom}L${cx + rx} ${top}A${rx} ${ry} 0 0 1 ${cx} ${top + ry}Z`;
  const cone = `M${cx - rx - 1.5} ${top}A${rx + 1.5} ${ry + 0.9} 0 0 0 ${cx + rx + 1.5} ${top}L${cx} ${top - coneH}Z`;
  const coneShade = `M${cx} ${top + ry + 0.9}A${rx + 1.5} ${ry + 0.9} 0 0 0 ${cx + rx + 1.5} ${top}L${cx} ${top - coneH}Z`;
  const band = (y: number): string => `M${cx - rx} ${y}A${rx} ${ry} 0 0 0 ${cx + rx} ${y}`;
  return <g>
    {legs.map(([x, y], i) => <path key={i} d={`M${x} ${y}L${x} ${y - legH}`} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />)}
    <path d={`M${legs[0][0]} ${legs[0][1] - legH}L${legs[1][0]} ${legs[1][1]}M${legs[1][0]} ${legs[1][1] - legH}L${legs[2][0]} ${legs[2][1]}`} stroke={INK} strokeWidth={W.hair} />
    <path d={body} fill={TINTS.sky.left} />
    <path d={bodyShade} fill={TINTS.sky.right} />
    <path d={band(top + bodyH / 3)} fill="none" stroke={INK} strokeWidth={W.hair} />
    <path d={band(top + (2 * bodyH) / 3)} fill="none" stroke={INK} strokeWidth={W.hair} />
    <path d={`M${cx - rx * 0.62} ${top + 6}L${cx - rx * 0.62} ${bottom + 7}M${cx - rx * 0.38} ${top + 7}L${cx - rx * 0.38} ${bottom + 8}`} stroke={INK} strokeWidth={W.hair} />
    {[0.25, 0.5, 0.75].map((k) => <path key={k} d={`M${cx - rx * 0.62} ${top + 6 + bodyH * k}L${cx - rx * 0.38} ${top + 7 + bodyH * k}`} stroke={INK} strokeWidth={W.hair} />)}
    <path d={body} fill="none" stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <path d={cone} fill={TINTS.sky.base} />
    <path d={coneShade} fill={TINTS.sky.right} />
    <path d={cone} fill="none" stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <circle cx={cx} cy={top - coneH - 1.5} r={2.2} fill={INK} />
  </g>;
}
