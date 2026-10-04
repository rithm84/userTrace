import type { JSX, ReactNode } from 'react';

import { faceEllipse, faceLine, faceRect, faceRoundRect, type Face } from './iso';
import { cycleAttributes } from './cycles';
import type { AppKind, Facade } from './model';
import { GLASS, GLASS_LIT, INK, TINTS, W, type Tint } from './paint';

/** One storey in grid units, and the taller ground floor of a shop. */
const FLOOR = 3.2;
const GROUND_FLOOR = 3.8;
const TEXT = '#c9ccd3';
const CARD = '#f1f2f5';
const DOTS = [TINTS.coral.base, TINTS.peach.base, TINTS.mint.base] as const;
const AVATARS = [TINTS.coral.base, TINTS.sky.base, TINTS.mint.base, TINTS.peach.base, TINTS.lilac.base, TINTS.aqua.base] as const;

type Rect = Readonly<{ u: number; v: number; w: number; h: number }>;

/** Everything painted on one face of a building; ink edges are drawn over it by the building. */
export function FacadeArt(props: Readonly<{ face: Face; facade: Facade; id: string; seed: number }>): JSX.Element | null {
  const { face, facade } = props;
  switch (facade.kind) {
    case 'blank':
      return null;
    case 'windows':
      return <Windows face={face} from={0.9} lit={facade.lit} door={facade.door} />;
    case 'app':
      // a one-storey wall is too short for a browser window, so its dashboard is a wall-mounted screen
      return facade.app === 'dashboard' && face.height < 5
        ? <DashboardScreen face={face} seed={props.seed} />
        : <AppPanel face={face} rect={{ u: 0.45, v: 0.9, w: face.width - 0.9, h: face.height - 1.6 }} app={facade.app} id={props.id} seed={props.seed} />;
    case 'shop':
      return <>
        <Storefront face={face} awning={facade.awning} />
        {facade.app !== null && face.height > GROUND_FLOOR + 2.6
          ? <AppPanel face={face} rect={{ u: 0.45, v: GROUND_FLOOR + 0.9, w: face.width - 0.9, h: face.height - GROUND_FLOOR - 1.6 }} app={facade.app} id={props.id} seed={props.seed} />
          : <Windows face={face} from={GROUND_FLOOR + 0.6} lit={[]} door={false} />}
      </>;
    case 'house':
      return <House face={face} />;
  }
}

/**
 * A grid of framed windows from `from` upward, each with a mullion, a sill and a glint; the `lit`
 * indices glow and flicker on twos. With `door`, the first ground-floor bay is an entrance.
 */
function Windows(props: Readonly<{ face: Face; from: number; lit: readonly number[]; door: boolean }>): JSX.Element {
  const { face } = props;
  const cols = Math.max(1, Math.floor((face.width - 0.6) / 1.9));
  const colW = (face.width - 0.6) / cols;
  const winW = Math.min(1.25, colW * 0.62);
  const winH = FLOOR * 0.56;
  // every storey whose window tops out under the cornice line (0.5 below the roof) with room to spare
  const rows = Math.max(0, Math.floor((face.height - 0.6 - props.from - winH) / FLOOR) + 1);
  const panes: ReactNode[] = [];
  let index = 0;
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const u = 0.3 + c * colW + (colW - winW) / 2;
      const v = props.from + r * FLOOR;
      const lit = props.lit.includes(index);
      if (props.door && r === 0 && c === 0 && face.width >= 3.4) {
        panes.push(<Entrance key="door" face={face} u={0.3 + (colW - 1.2) / 2} />);
        index += 1;
        continue;
      }
      panes.push(<g key={index}>
        <path d={faceRect(face, u - 0.1, v - 0.2, winW + 0.2, 0.2)} fill="#ffffff" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
        <path d={faceRect(face, u, v, winW, winH)} fill={GLASS} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
        {lit ? <path {...cycleAttributes({ period: 7000, offset: (index % 5) * 1300, from: 0.84, span: 0.74 })} d={faceRect(face, u, v, winW, winH)} fill={GLASS_LIT} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" /> : null}
        <path d={faceLine(face, [[u + winW / 2, v], [u + winW / 2, v + winH]])} stroke={INK} strokeWidth={W.hair} />
        <path d={faceLine(face, [[u, v + winH * 0.62], [u + winW, v + winH * 0.62]])} stroke={INK} strokeWidth={W.hair} />
        <path d={faceLine(face, [[u + winW * 0.12, v + winH * 0.5], [u + winW * 0.36, v + winH * 0.2]])} stroke="#ffffff" strokeWidth={W.fine} strokeLinecap="round" />
      </g>);
      index += 1;
    }
  }
  return <g>{panes}</g>;
}

/** A glazed entrance with a frame, a push bar and a shallow pale canopy that stays quiet next to the windows. */
function Entrance(props: Readonly<{ face: Face; u: number }>): JSX.Element {
  const { face, u } = props;
  return <g>
    <path d={faceRect(face, u - 0.12, 0, 1.44, 2.62)} fill="#ffffff" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={faceRect(face, u, 0, 1.2, 2.45)} fill={GLASS} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={faceLine(face, [[u + 0.6, 0], [u + 0.6, 2.45]])} stroke={INK} strokeWidth={W.hair} />
    <path d={faceLine(face, [[u + 0.25, 1.15], [u + 0.48, 1.15]])} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />
    <path d={`M${pt(face.at(u - 0.2, 2.85))}L${pt(face.at(u + 1.4, 2.85))}L${pt(face.off(u + 1.4, 2.75, 0.45))}L${pt(face.off(u - 0.2, 2.75, 0.45))}Z`} fill="#e4e5e9" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
  </g>;
}

/** A product window on the wall: title bar with three dots, and the app itself inside. */
function AppPanel(props: Readonly<{ face: Face; rect: Rect; app: AppKind; id: string; seed: number }>): JSX.Element {
  const { face, rect } = props;
  const bar = Math.min(1.1, rect.h * 0.12);
  const content: Rect = { u: rect.u + 0.35, v: rect.v + 0.35, w: rect.w - 0.7, h: rect.h - bar - 0.6 };
  return <g>
    <path d={faceRoundRect(face, rect.u, rect.v, rect.w, rect.h, 0.45)} fill="#ffffff" stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <path d={faceLine(face, [[rect.u, rect.v + rect.h - bar], [rect.u + rect.w, rect.v + rect.h - bar]])} stroke={INK} strokeWidth={W.fine} />
    {DOTS.map((colour, i) => <path key={colour} d={faceEllipse(face, rect.u + 0.55 + i * 0.5, rect.v + rect.h - bar / 2, 0.17, 0.17)} fill={colour} stroke={INK} strokeWidth={W.hair} />)}
    <AppContent face={face} rect={content} app={props.app} id={props.id} seed={props.seed} />
  </g>;
}

function AppContent(props: Readonly<{ face: Face; rect: Rect; app: AppKind; id: string; seed: number }>): JSX.Element {
  switch (props.app) {
    case 'chat': return <Chat {...props} />;
    case 'dashboard': return <Dashboard {...props} />;
    case 'gallery': return <Gallery {...props} />;
    case 'settings': return <Settings {...props} />;
    case 'form': return <Form {...props} />;
    case 'profile': return <Profile {...props} />;
  }
}

/**
 * A dashboard for a one-storey wall: a screen hung at eye level in a dark bezel, showing one bold
 * bar chart whose five bars climb left to right and step to new values on the city's clock.
 */
function DashboardScreen(props: Readonly<{ face: Face; seed: number }>): JSX.Element {
  const { face } = props;
  const random = seeded(props.seed);
  const w = Math.min(face.width - 1.4, 4.6);
  const screen: Rect = { u: (face.width - w) / 2, v: 1.05, w, h: 2.1 };
  const glass: Rect = { u: screen.u + 0.16, v: screen.v + 0.16, w: screen.w - 0.32, h: screen.h - 0.32 };
  const chart: Rect = { u: glass.u + 0.3, v: glass.v + 0.28, w: glass.w - 0.6, h: glass.h - 0.6 };
  const bars = 5;
  const gap = 0.14;
  const barW = (chart.w - gap * (bars - 1)) / bars;
  const colours = [TINTS.coral.base, TINTS.sky.base, TINTS.mint.base] as const;
  const frames = [0, 1, 2].map(() => Array.from({ length: bars }, (_, i) => Math.min(1, 0.28 + i * 0.13 + random() * 0.22)));
  return <g>
    <path d={faceRoundRect(face, screen.u, screen.v, screen.w, screen.h, 0.28)} fill={INK} />
    <path d={faceRoundRect(face, glass.u, glass.v, glass.w, glass.h, 0.14)} fill="#ffffff" />
    {frames.map((heights, f) => <g key={f} {...cycleAttributes({ period: 4800, offset: 0, from: f / 3, span: 1 / 3 })}>
      {heights.map((height, i) => <path key={i} d={faceRect(face, chart.u + i * (barW + gap), chart.v, barW, chart.h * height)} fill={colours[i % 3]} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />)}
    </g>)}
    <path d={faceLine(face, [[chart.u - 0.1, chart.v], [chart.u + chart.w + 0.1, chart.v]])} stroke={INK} strokeWidth={W.fine} strokeLinecap="round" />
  </g>;
}

/** A deterministic 0..1 sequence, so every render of the city is identical. */
export function seeded(seed: number): () => number {
  let state = (seed * 2654435761) >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return ((state >>> 0) % 10000) / 10000;
  };
}

const CHAT_ROW = 1.75;
/** The chat scrolls through this many distinct messages, then repeats seamlessly. */
const CHAT_CYCLE = 4;

/** Messages stacked in a clipped column that scrolls up one message at a time. */
function Chat(props: Readonly<{ face: Face; rect: Rect; id: string; seed: number }>): JSX.Element {
  const { face, rect } = props;
  const random = seeded(props.seed);
  const base = Array.from({ length: CHAT_CYCLE }, () => ({ mine: random() < 0.3, width: Math.min(rect.w - 1.4, 1.6 + random() * (rect.w - 2.2)), avatar: AVATARS[Math.floor(random() * AVATARS.length)] }));
  const rows = Math.ceil(rect.h / CHAT_ROW) + CHAT_CYCLE + 1;
  const clipId = `city-clip-${props.id}`;
  const step = face.at(0, 0)[1] - face.at(0, CHAT_ROW)[1];
  const messages: ReactNode[] = [];
  for (let i = 0; i < rows; i += 1) {
    const { mine, width, avatar } = base[i % CHAT_CYCLE];
    const top = rect.v + rect.h - i * CHAT_ROW;
    const bubbleU = mine ? rect.u + rect.w - width : rect.u + 1.05;
    messages.push(<g key={i}>
      {mine ? null : <path d={faceEllipse(face, rect.u + 0.42, top - 0.62, 0.38, 0.38)} fill={avatar} stroke={INK} strokeWidth={W.hair} />}
      <path d={faceRoundRect(face, bubbleU, top - 1.25, width, 1.05, 0.42)} fill={mine ? TINTS.coral.top : CARD} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
      <path d={faceLine(face, [[bubbleU + 0.35, top - 0.72], [bubbleU + Math.max(0.6, width * 0.7), top - 0.72]])} stroke={mine ? TINTS.coral.base : TEXT} strokeWidth={W.line} strokeLinecap="round" />
    </g>);
  }
  return <g>
    <clipPath id={clipId}><path d={faceRect(face, rect.u - 0.1, rect.v, rect.w + 0.2, rect.h)} /></clipPath>
    <g clipPath={`url(#${clipId})`}>
      <g className="city-chat" style={{ ['--city-chat-step' as string]: `${step.toFixed(2)}px` }}>{messages}</g>
    </g>
  </g>;
}

/**
 * The shop's screen, where the customer sees the change land: a mint check badge beside a bold
 * title and a grey detail line, over a coral button, read as "validated" at a glance.
 */
function Dashboard(props: Readonly<{ face: Face; rect: Rect; seed: number }>): JSX.Element {
  const { face, rect } = props;
  const top = rect.v + rect.h;
  const badge = { u: rect.u + 0.8, v: top - 0.85, r: 0.6 };
  const textU = badge.u + badge.r + 0.45;
  const button = { u: rect.u + 0.15, v: rect.v + 0.15, w: rect.w - 0.3, h: Math.min(0.75, rect.h * 0.3) };
  return <g>
    <path d={faceEllipse(face, badge.u, badge.v, badge.r, badge.r)} fill={TINTS.mint.base} stroke={INK} strokeWidth={W.fine} />
    <path d={faceLine(face, [[badge.u - 0.28, badge.v + 0.02], [badge.u - 0.06, badge.v - 0.2], [badge.u + 0.3, badge.v + 0.22]])} fill="none" stroke={INK} strokeWidth={W.heavy} strokeLinecap="round" strokeLinejoin="round" />
    <path d={faceLine(face, [[textU, badge.v + 0.25], [rect.u + rect.w - 0.4, badge.v + 0.25]])} stroke={INK} strokeWidth={W.heavy} strokeLinecap="round" />
    <path d={faceLine(face, [[textU, badge.v - 0.25], [textU + (rect.u + rect.w - 0.4 - textU) * 0.7, badge.v - 0.25]])} stroke={TEXT} strokeWidth={W.fine} strokeLinecap="round" />
    <path d={faceRoundRect(face, button.u, button.v, button.w, button.h, button.h / 2)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={faceLine(face, [[button.u + button.w * 0.32, button.v + button.h / 2], [button.u + button.w * 0.68, button.v + button.h / 2]])} stroke={INK} strokeWidth={W.fine} strokeLinecap="round" />
  </g>;
}

function Gallery(props: Readonly<{ face: Face; rect: Rect }>): JSX.Element {
  const { face, rect } = props;
  const cols = rect.w > 4 ? 2 : 1;
  const cardW = (rect.w - (cols - 1) * 0.35) / cols;
  const cardH = Math.min(cardW * 0.78, 2.6);
  const rows = Math.max(1, Math.floor((rect.h + 0.35) / (cardH + 0.35)));
  const cards: ReactNode[] = [];
  for (let r = 0; r < rows; r += 1) for (let c = 0; c < cols; c += 1) {
    const u = rect.u + c * (cardW + 0.35);
    const v = rect.v + rect.h - (r + 1) * cardH - r * 0.35;
    cards.push(<g key={`${r}-${c}`}>
      <path d={faceRoundRect(face, u, v, cardW, cardH, 0.3)} fill="#eaf5fe" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
      <path d={faceLine(face, [[u + 0.2, v + 0.25], [u + cardW * 0.42, v + cardH * 0.62], [u + cardW * 0.62, v + cardH * 0.38], [u + cardW * 0.78, v + cardH * 0.52], [u + cardW - 0.2, v + 0.25]]) + 'Z'} fill={TINTS.mint.base} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
      <path d={faceEllipse(face, u + cardW * 0.74, v + cardH * 0.76, 0.28, 0.28)} fill={TINTS.peach.base} stroke={INK} strokeWidth={W.hair} />
    </g>);
  }
  return <g>{cards}</g>;
}

function Settings(props: Readonly<{ face: Face; rect: Rect; seed: number }>): JSX.Element {
  const { face, rect } = props;
  const random = seeded(props.seed + 7);
  const rowH = 1.25;
  const rows = Math.max(1, Math.floor(rect.h / rowH));
  return <g>{Array.from({ length: rows }, (_, i) => {
    const v = rect.v + rect.h - (i + 1) * rowH + 0.2;
    const on = random() > 0.4;
    const pillU = rect.u + rect.w - 1.45;
    return <g key={i} className={i === 1 ? 'city-toggle' : undefined}>
      <path d={faceLine(face, [[rect.u + 0.1, v + 0.42], [rect.u + Math.max(0.8, rect.w - 2.4), v + 0.42]])} stroke={TEXT} strokeWidth={W.line} strokeLinecap="round" />
      <path d={faceRoundRect(face, pillU, v + 0.05, 1.35, 0.75, 0.37)} fill={on ? TINTS.mint.base : '#e3e4e8'} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
      <path d={faceEllipse(face, on ? pillU + 1.0 : pillU + 0.37, v + 0.42, 0.27, 0.27)} fill="#ffffff" stroke={INK} strokeWidth={W.hair} />
    </g>;
  })}</g>;
}

function Form(props: Readonly<{ face: Face; rect: Rect }>): JSX.Element {
  const { face, rect } = props;
  const fieldH = 0.85;
  const fields = Math.max(1, Math.floor((rect.h - 1.4) / 1.55));
  return <g>
    {Array.from({ length: fields }, (_, i) => {
      const v = rect.v + rect.h - (i + 1) * 1.55;
      return <g key={i}>
        <path d={faceLine(face, [[rect.u + 0.05, v + fieldH + 0.35], [rect.u + rect.w * 0.4, v + fieldH + 0.35]])} stroke={TEXT} strokeWidth={W.fine} strokeLinecap="round" />
        <path d={faceRoundRect(face, rect.u, v, rect.w, fieldH, 0.25)} fill="#ffffff" stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
      </g>;
    })}
    <path d={faceRoundRect(face, rect.u, rect.v, Math.min(rect.w, 3), 0.95, 0.3)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
  </g>;
}

function Profile(props: Readonly<{ face: Face; rect: Rect }>): JSX.Element {
  const { face, rect } = props;
  const r = Math.min(1, rect.w * 0.2);
  const top = rect.v + rect.h;
  return <g>
    <path d={faceEllipse(face, rect.u + r + 0.1, top - r - 0.1, r, r)} fill={TINTS.lilac.base} stroke={INK} strokeWidth={W.hair} />
    <path d={faceLine(face, [[rect.u + 2 * r + 0.5, top - 0.6], [rect.u + rect.w - 0.2, top - 0.6]])} stroke={INK} strokeWidth={W.heavy} strokeLinecap="round" />
    <path d={faceLine(face, [[rect.u + 2 * r + 0.5, top - 1.3], [rect.u + rect.w * 0.75, top - 1.3]])} stroke={TEXT} strokeWidth={W.line} strokeLinecap="round" />
    {[0, 1, 2].map((i) => {
      const w = (rect.w - 0.6) / 3;
      const v = top - 2 * r - 1.6;
      return v > rect.v + 0.2 ? <path key={i} d={faceRoundRect(face, rect.u + i * (w + 0.3), Math.max(rect.v, v - 1.4), w, Math.min(1.4, v - rect.v), 0.3)} fill={CARD} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" /> : null;
    })}
  </g>;
}

/** A lit shop window and door on the ground floor under a striped awning. */
function Storefront(props: Readonly<{ face: Face; awning: Tint }>): JSX.Element {
  const { face } = props;
  const doorW = 1.25;
  const doorU = face.width - doorW - 0.6;
  const stripes = Math.max(4, Math.round(face.width / 0.9));
  const stripeW = (face.width - 0.4) / stripes;
  const top = 3.55;
  const drop = 2.85;
  const reach = 1.25;
  return <g>
    <path d={faceRect(face, 0.45, 0.35, doorU - 0.85, 2.55)} fill={GLASS_LIT} stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <path d={faceLine(face, [[0.45 + (doorU - 0.85) / 2, 0.35], [0.45 + (doorU - 0.85) / 2, 2.9]])} stroke={INK} strokeWidth={W.fine} />
    <path d={faceRect(face, doorU, 0, doorW, 2.65)} fill="#ffffff" stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <path d={faceRect(face, doorU + 0.25, 1.3, doorW - 0.5, 1.0)} fill={GLASS} stroke={INK} strokeWidth={W.hair} />
    {Array.from({ length: stripes }, (_, i) => {
      const u0 = 0.2 + i * stripeW;
      const u1 = u0 + stripeW;
      const fill = i % 2 === 0 ? TINTS[props.awning].base : '#ffffff';
      return <path key={i} d={`M${pt(face.at(u0, top))}L${pt(face.at(u1, top))}L${pt(face.off(u1, drop, reach))}L${pt(face.off(u0, drop, reach))}Z`} fill={fill} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />;
    })}
    <path d={`M${pt(face.off(0.2, drop, reach))}L${pt(face.off(face.width - 0.2, drop, reach))}L${pt(face.off(face.width - 0.2, drop - 0.45, reach))}L${pt(face.off(0.2, drop - 0.45, reach))}Z`} fill={TINTS[props.awning].base} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    <path d={`M${pt(face.at(0.2, top))}L${pt(face.at(face.width - 0.2, top))}L${pt(face.off(face.width - 0.2, drop, reach))}L${pt(face.off(0.2, drop, reach))}Z`} fill="none" stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
  </g>;
}

/** A front door with a small canopy and two windows with flower boxes. */
function House(props: Readonly<{ face: Face }>): JSX.Element {
  const { face } = props;
  const doorU = face.width * 0.22;
  const winU = face.width * 0.58;
  return <g>
    <path d={faceRect(face, doorU, 0.9, 1.3, 2.5)} fill={TINTS.sky.base} stroke={INK} strokeWidth={W.line} strokeLinejoin="round" />
    <path d={faceEllipse(face, doorU + 1.05, 2.1, 0.09, 0.09)} fill={INK} />
    <path d={`M${pt(face.at(doorU - 0.25, 3.75))}L${pt(face.at(doorU + 1.55, 3.75))}L${pt(face.off(doorU + 1.55, 3.45, 0.8))}L${pt(face.off(doorU - 0.25, 3.45, 0.8))}Z`} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
    {[0, 1].map((floor) => <g key={floor}>
      <path d={faceRect(face, winU, 1.5 + floor * FLOOR, 1.5, 1.45)} fill={floor === 0 ? GLASS_LIT : GLASS} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
      <path d={faceLine(face, [[winU + 0.75, 1.5 + floor * FLOOR], [winU + 0.75, 2.95 + floor * FLOOR]])} stroke={INK} strokeWidth={W.hair} />
      <path d={faceRect(face, winU - 0.1, 1.15 + floor * FLOOR, 1.7, 0.38)} fill={TINTS.coral.base} stroke={INK} strokeWidth={W.hair} strokeLinejoin="round" />
    </g>)}
    {face.height > 7.5 ? <path d={faceRect(face, doorU + 0.1, 1.5 + FLOOR, 1.1, 1.45)} fill={GLASS} stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" /> : null}
  </g>;
}

function pt(p: readonly [number, number]): string {
  return `${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
}
