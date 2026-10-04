import type { JSX, ReactNode } from 'react';

import { ASPHALT_ID, PAVING_ID, Solid } from './buildings';
import { closedPath, groundRect, iso, isoDelta, openPath } from './iso';
import type { Ground } from './model';
import { CORAL_PATH, INK, STREET, STREET_LINE, TINTS, W } from './paint';

/** Streets, sidewalks, the coral route and the lots, drawn flat beneath everything else. */
export function GroundArt(props: Readonly<{ pieces: readonly Ground[] }>): JSX.Element {
  const order: readonly Ground['kind'][] = ['street', 'manhole', 'crossing', 'path', 'slab', 'lawn', 'lot', 'deck'];
  return <g>{order.flatMap((kind) => props.pieces.filter((piece) => piece.kind === kind).map((piece, i) => <Piece key={`${kind}-${i}`} piece={piece} />))}</g>;
}

function Piece(props: Readonly<{ piece: Ground }>): JSX.Element {
  const g = props.piece;
  switch (g.kind) {
    case 'street': {
      const lanes: ReactNode[] = [];
      if (g.lanes) {
        const length = g.along === 'x' ? g.w : g.d;
        for (let t = 0.8; t < length - 1.2; t += 2.4) {
          const a = g.along === 'x' ? iso([g.x + t, g.y + g.d / 2, 0]) : iso([g.x + g.w / 2, g.y + t, 0]);
          const b = g.along === 'x' ? iso([g.x + t + 1.2, g.y + g.d / 2, 0]) : iso([g.x + g.w / 2, g.y + t + 1.2, 0]);
          lanes.push(<path key={t} d={openPath([a, b])} stroke={STREET_LINE} strokeWidth={2.4} strokeLinecap="round" />);
        }
      }
      return <g><path d={groundRect(g.x, g.y, g.w, g.d, 0)} fill={STREET} /><path d={groundRect(g.x, g.y, g.w, g.d, 0)} fill={`url(#${ASPHALT_ID})`} />{lanes}</g>;
    }
    case 'crossing': {
      const stripes: ReactNode[] = [];
      const length = g.along === 'x' ? g.w : g.d;
      for (let t = 0.3; t < length - 0.3; t += 0.9) {
        stripes.push(<path key={t} d={g.along === 'x' ? groundRect(g.x + t, g.y, 0.5, g.d, 0) : groundRect(g.x, g.y + t, g.w, 0.5, 0)} fill="#ffffff" />);
      }
      return <g>{stripes}</g>;
    }
    case 'path': {
      // chevrons march one spacing along the route and repeat, clipped to the paint, so the street flows toward the next station
      const spacing = 3.2;
      const chevrons: ReactNode[] = [];
      const length = g.along === 'x' ? g.w : g.d;
      const across = g.along === 'x' ? g.d : g.w;
      for (let t = 1.6 - spacing; t < length + spacing; t += spacing) {
        const p = (along: number, side: number) => g.along === 'x' ? iso([g.x + along, g.y + across / 2 + side, 0]) : iso([g.x + across / 2 + side, g.y + along, 0]);
        const tip = t + 0.55 * g.sign;
        chevrons.push(<path key={t} d={openPath([p(t - 0.55 * g.sign, -0.7), p(tip, 0), p(t - 0.55 * g.sign, 0.7)])} fill="none" stroke="#ffffff" strokeWidth={3.2} strokeLinecap="round" strokeLinejoin="round" />);
      }
      const [dx, dy] = g.along === 'x' ? isoDelta(spacing * g.sign, 0, 0) : isoDelta(0, spacing * g.sign, 0);
      const clipId = `city-path-${g.along}-${g.x}-${g.y}`.replace(/\./g, '_');
      return <g>
        <clipPath id={clipId}><path d={groundRect(g.x, g.y, g.w, g.d, 0)} /></clipPath>
        <path d={groundRect(g.x, g.y, g.w, g.d, 0)} fill={CORAL_PATH} />
        <path d={groundRect(g.x, g.y, g.w, g.d, 0)} fill="none" stroke={INK} strokeWidth={W.hair} opacity={0.35} />
        <g clipPath={`url(#${clipId})`}>
          <g className="city-march" style={{ ['--city-march-x' as string]: `${dx.toFixed(2)}px`, ['--city-march-y' as string]: `${dy.toFixed(2)}px` }}>{chevrons}</g>
        </g>
      </g>;
    }
    case 'slab':
      return <g>
        <Solid box={{ x: g.x, y: g.y, z: 0, w: g.w, d: g.d, h: 0.3 }} tint="white" weight={W.line} />
        <path d={groundRect(g.x + 0.08, g.y + 0.08, g.w - 0.16, g.d - 0.16, 0.3)} fill={`url(#${PAVING_ID})`} />
      </g>;
    case 'manhole': {
      const [cx, cy] = iso([g.x, g.y, 0]);
      return <g>
        <ellipse cx={cx} cy={cy} rx={11} ry={6.35} fill="#dcdee3" stroke={INK} strokeWidth={W.hair} />
        <ellipse cx={cx} cy={cy} rx={7.6} ry={4.4} fill="none" stroke={INK} strokeWidth={W.hair} opacity={0.5} />
        <path d={`M${cx - 6} ${cy}L${cx + 6} ${cy}M${cx - 3.4} ${cy - 2.5}L${cx + 3.4} ${cy + 2.5}`} stroke={INK} strokeWidth={W.hair} opacity={0.5} />
      </g>;
    }
    case 'lawn':
      return <Solid box={{ x: g.x, y: g.y, z: 0.3, w: g.w, d: g.d, h: 0.2 }} tint="mint" weight={W.fine} />;
    case 'deck': {
      // a café terrace: warm planks running along gy, laid at the buildings' base level so its
      // edges run straight into the walls beside it
      const top = 0;
      const planks = Math.floor(g.w / 0.6);
      return <g>
        <path d={groundRect(g.x, g.y, g.w, g.d, top)} fill="#f6e6d6" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
        {Array.from({ length: planks - 1 }, (_, i) => <path key={i} d={openPath([iso([g.x + (i + 1) * (g.w / planks), g.y, top]), iso([g.x + (i + 1) * (g.w / planks), g.y + g.d, top])])} stroke="#e2c7ad" strokeWidth={W.hair} />)}
      </g>;
    }
    case 'lot':
      return <g>
        <path d={groundRect(g.x, g.y, g.w, g.d, 0.3)} fill="#efe4d6" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
        {Array.from({ length: Math.round(g.w * g.d / 3) }, (_, i) => {
          const [x, y] = iso([g.x + 0.4 + ((i * 5.13) % (g.w - 0.8)), g.y + 0.4 + ((i * 2.71) % (g.d - 0.8)), 0.3]);
          return <circle key={i} cx={x} cy={y} r={1.1} fill={INK} opacity={0.28} />;
        })}
        <path d={closedPath([iso([g.x, g.y, 0.3]), iso([g.x + g.w, g.y, 0.3]), iso([g.x + g.w, g.y + g.d, 0.3]), iso([g.x, g.y + g.d, 0.3])])} fill="none" stroke={TINTS.peach.base} strokeWidth={W.hair} />
      </g>;
  }
}
