'use client';

import { memo, useCallback, useEffect, useRef, useState, type JSX, type ReactNode, type RefObject } from 'react';

import { Cameo, ENTRANCE_MS } from '../pieces';
import { Building, CityDefs, Solid } from './buildings';
import { paintLevels } from './depth';
import { cityFit, DESIGN, NAV_CLEAR, type CityFit } from './fit';
import { skylineTop } from './skyline';
import { GroundArt } from './ground';
import { iso, isoDelta, openPath, screenBounds, type Box } from './iso';
import type { Item, Sky } from './model';
import { BuildToken, CaseShadow, CaseToken, SpeechBubble, Trike, TRIKE_SEAT } from './movers';
import { INK, W } from './paint';
import { Person } from './people';
import { CITY_PLAN, RIG_SIZE, SKY_SWELL } from './plan';
import { CraneHead, craneHead, CraneLoad, CraneMast, Frame, Planter, Tree } from './props';
import { readCycle, shownAt } from './cycles';
import { CLOUDS, SpriteImage } from './sprites';
import { agentPose, caseAt, craneAt, digAt, sameScene, sceneAt, trikeAt, type Scene } from './story';
import { Bench, Dig, Lamp, Table } from './street';
import './city.css';

type AgentItem = Extract<Item, { kind: 'agent' }>;
type TrikeItem = Extract<Item, { kind: 'trike' }>;
type CraneItem = Extract<Item, { kind: 'crane' }>;
type Layer = Readonly<{ kind: 'svg'; nodes: readonly ReactNode[]; bounds: Readonly<{ left: number; top: number; width: number; height: number }> }> | Readonly<{ kind: 'agent'; item: AgentItem; riding: boolean }>;

/** Where a rig's feet land in its square box, as a fraction of its height. */
const RIG_FOOT = 0.835;

const TRIKE = (() => {
  const item = CITY_PLAN.items.find((entry): entry is TrikeItem => entry.kind === 'trike');
  if (item === undefined) throw new Error('The city plan has no delivery trike.');
  return { x: item.box.x + item.box.w / 2, start: item.route[0], distance: item.route[0] - item.route[1] };
})();

const CRANE_BOX = (() => {
  const item = CITY_PLAN.items.find((entry): entry is CraneItem => entry.kind === 'crane');
  if (item === undefined) throw new Error('The city plan has no crane.');
  return item.box;
})();
/**
 * The jib's height; the trolley's underside, where the cable starts; and the highest the panel's
 * top rides, low enough that a length of cable, the hook and its two slings show above it.
 */
const JIB_Z = CRANE_BOX.h + 0.3;
const TROLLEY_BOTTOM = JIB_Z - 0.5;
const LOAD_REST_TOP = JIB_Z - 1.4;
/** How far above the panel's top the hook hangs, the slings running down to its two corners. */
const HOOK_ABOVE = 0.55;
const LOAD_HEIGHT = 1.2;
/** Where the panel's top sits against the frame's facade when lowered, and how far it lifts off. */
const LOAD_SET_TOP = 1.9 + LOAD_HEIGHT;
const LOAD_TRAVEL = LOAD_REST_TOP - LOAD_SET_TOP;

/** How much smaller the Case File is when it lies on the interview table, so it fits the table's top. */
const CASE_REST_SHRINK = 0.45;

/** Where a still city (reduced motion) is held: the fix has reached the customer, who validates it. */
const STILL_MS = 17600;
/** The hole aqua climbs out of: its ellipse in design pixels, and how far aqua sinks when out of sight. */
const HOLE = Object.freeze({ rx: 30, ry: 30 / 1.732, sunk: 86, risen: 14 });

/** Every rig in the city, including the trike's rider. */
const RIG_COUNT = CITY_PLAN.items.filter((item) => item.kind === 'agent' || item.kind === 'trike').length;

/**
 * The isometric city under the hero copy: SVG planes painted back to front, with the agents' Rive
 * rigs slotted between the planes at their depth so buildings in front of them still cover them.
 * One story clock (`story.ts`) plays the subhead in order: the Case File travels from station to
 * station and each station acts as it arrives; CSS handles the ambient loops (chat
 * scrolling, lit windows, trees, clouds, people). `live` pauses all of it; `still` holds the
 * validated moment for reduced motion.
 */
export const City = memo(function City(props: Readonly<{ live: boolean; still: boolean; onReady: () => void }>): JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState<CityFit | null>(null);
  const scene = useCityClock(props.live, props.still, host, fit !== null);
  const ready = useRef(new Set<string>());
  const { onReady } = props;
  const rigReady = useCallback((id: string): void => {
    ready.current.add(id);
    if (ready.current.size === RIG_COUNT) onReady();
  }, [onReady]);

  useEffect(() => {
    const hero = host.current?.parentElement ?? null;
    if (hero === null) throw new Error('Cannot fit the city: it has no hero to measure.');
    // the push last applied above the copy, so the copy's own position can be measured without it
    let pushed = 0;
    const measure = (): void => {
      const copy = hero.querySelector('[data-testid="hero-cta-row"]');
      if (copy === null) throw new Error('Cannot fit the city: the hero waitlist row is missing.');
      const stage = hero.getBoundingClientRect();
      // not laid out yet (or hidden): the observer measures again once the hero has a box
      if (stage.width === 0 || stage.height === 0) return;
      const next = cityFit(stage.width, stage.height, copy.getBoundingClientRect().bottom - stage.top - pushed, skylineTop);
      pushed = next.push;
      hero.style.setProperty('--hero-push', `${next.push.toFixed(1)}px`);
      setFit(next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(hero);
    return () => observer.disconnect();
  }, []);

  return <div ref={host} className={'city' + (props.live ? '' : ' is-paused')} data-testid="hero-city" aria-hidden="true">
    {fit !== null ? <div className="city-box" style={{ left: fit.left / fit.scale, top: fit.top / fit.scale, width: DESIGN.width, height: DESIGN.height, zoom: fit.scale }}>
      <Plane bounds={FULL}><CityDefs /><GroundArt pieces={CITY_PLAN.ground} /></Plane>
      {/* the clouds are sky: behind every building, peeking over the side towers' roofs */}
      <SkyPlane bounds={SKY_BOUNDS} drop={skyDrop(fit)}><Clouds entries={CITY_PLAN.sky} /></SkyPlane>
      <CaseShadow />
      {LAYERS.map((layer, index) => layer.kind === 'svg'
        ? <Plane key={index} bounds={layer.bounds}>{layer.nodes}</Plane>
        : <Rig key={index} item={layer.item} riding={layer.riding} scene={scene} live={props.live} zoom={fit.scale} onReady={rigReady} />)}
      <Plane bounds={BUBBLES}>
        {CITY_PLAN.bubbles.map((bubble, i) => <SpeechBubble key={bubble.id} bubble={bubble} index={i} shown={bubbleShown(bubble.cue, scene)} />)}
      </Plane>
      <CaseToken />
      <BuildToken />
    </div> : null}
  </div>;
});

/**
 * Clouds drifting on their own beats behind the buildings: a slow sideways drift, a gentler bob and
 * a slight swell about the cloud's base, each on a period the others never line up with, so no two
 * clouds move alike.
 * These stay CSS animations: the compositor moves them, where the city's clock would re-raster the
 * planes they sit in.
 */
function Clouds(props: Readonly<{ entries: readonly Sky[] }>): JSX.Element {
  return <g>{props.entries.map((entry, i) => {
    const seed = entry.at[0] + entry.at[1] * 7;
    return <g key={i} className="city-drift" style={{ ['--city-drift' as string]: `${entry.drift}px`, animationDelay: `${-(seed % 26).toFixed(1)}s` }}>
      <g className="city-bob" style={{ animationDuration: `${(6.2 + (seed % 23) / 10).toFixed(1)}s`, animationDelay: `${-(seed % 9).toFixed(1)}s` }}>
        <g className="city-swell" style={{ ['--city-swell' as string]: SKY_SWELL, transformOrigin: `${entry.at[0]}px ${entry.at[1]}px`, animationDuration: `${(9.5 + (seed % 37) / 10).toFixed(1)}s`, animationDelay: `${-(seed % 13).toFixed(1)}s` }}>
          <SpriteImage sprite={{ ...CLOUDS[entry.cloud], scale: CLOUDS[entry.cloud].scale * entry.size }} at={entry.at} flip={i % 2 === 1} />
        </g>
      </g>
    </g>;
  })}</g>;
}

/** Where the clouds can reach as they drift. */
const SKY_BOUNDS: Bounds = (() => {
  const boxes = CITY_PLAN.sky.map((entry) => {
    const sprite = CLOUDS[entry.cloud];
    const scale = sprite.scale * entry.size;
    const left = entry.at[0] - sprite.ax * scale - entry.drift;
    const top = entry.at[1] - sprite.ay * scale;
    return { left, top, right: left + sprite.width * scale + 2 * entry.drift, bottom: top + sprite.height * scale };
  });
  const left = Math.floor(Math.min(...boxes.map((b) => b.left)));
  const top = Math.floor(Math.min(...boxes.map((b) => b.top)));
  return { left, top, width: Math.ceil(Math.max(...boxes.map((b) => b.right))) - left, height: Math.ceil(Math.max(...boxes.map((b) => b.bottom))) - top };
})();

/** How far the clouds hang below where the plan draws them: enough that the highest keeps the buildings' `NAV_CLEAR` under the navbar. */
function skyDrop(fit: CityFit): number {
  const visibleTop = -fit.top / fit.scale;
  return Math.max(0, visibleTop + NAV_CLEAR / fit.scale - SKY_BOUNDS.top);
}

/** A plane of clouds, drawn at its plan position and hung `drop` design pixels lower. */
function SkyPlane(props: Readonly<{ bounds: Bounds; drop: number; children: ReactNode }>): JSX.Element {
  const b = props.bounds;
  return <svg className="city-plane" style={{ left: b.left, top: b.top + props.drop }} width={b.width} height={b.height} viewBox={`${b.left} ${b.top} ${b.width} ${b.height}`}>{props.children}</svg>;
}

function Plane(props: Readonly<{ bounds: Bounds; children: ReactNode }>): JSX.Element {
  const b = props.bounds;
  return <svg className="city-plane" style={{ left: b.left, top: b.top }} width={b.width} height={b.height} viewBox={`${b.left} ${b.top} ${b.width} ${b.height}`}>{props.children}</svg>;
}

function bubbleShown(cue: 'feed' | 'customer' | 'pain' | 'reply', scene: Scene): boolean {
  switch (cue) {
    case 'feed': return true;
    case 'customer': return scene.customerTalks;
    case 'pain': return scene.painShown;
    case 'reply': return scene.replyShown;
  }
}

/**
 * One agent's rig, feet on its ground point. The trike's rider moves with the trike; the agent in
 * the dig is clipped along the hole's front rim and rises out of it on the clock.
 */
function Rig(props: Readonly<{ item: AgentItem; riding: boolean; scene: Scene; live: boolean; zoom: number; onReady: (id: string) => void }>): JSX.Element {
  const { item, onReady } = props;
  const [x, y] = iso([item.box.x + item.box.w / 2, item.box.y + item.box.d / 2, props.riding ? TRIKE_SEAT : 0]);
  const pose = agentPose(item.cue, props.scene);
  const ready = useCallback(() => onReady(item.id), [onReady, item.id]);
  const buried = item.cue === 'uncover';
  // a paused city parks every rig once its entrance has played; aqua also parks at once while it is down the manhole
  const park = !props.live ? ENTRANCE_MS : buried && !props.scene.digShown ? 0 : null;
  const cameo = <Cameo colour={item.colour} state={pose.state} park={park} size={item.size} gazeX={pose.gazeX} gazeY={pose.gazeY} testId={`city-${item.id}`} zoom={props.zoom} onReady={ready} eager style={{ left: 0, top: 0 }} />;
  return <div data-mover={props.riding ? 'trike' : undefined} className="city-rig" style={{ left: x - item.size / 2, top: y - item.size * RIG_FOOT, width: item.size, height: item.size, clipPath: buried ? holeClip(item.size) : undefined }}>
    {buried ? <div data-mover="dig" style={{ position: 'absolute', inset: 0, transform: `translateY(${HOLE.sunk}px)` }}>{cameo}</div> : cameo}
  </div>;
}

/** Everything above the hole's front rim, in the rig box's own pixels: the part of aqua out of the ground. */
function holeClip(size: number): string {
  const cx = size / 2;
  const cy = size * RIG_FOOT;
  const arc = Array.from({ length: 13 }, (_, i) => {
    const angle = (i / 12) * Math.PI;
    return `${(cx + Math.cos(angle) * HOLE.rx).toFixed(1)}px ${(cy + Math.sin(angle) * HOLE.ry).toFixed(1)}px`;
  });
  return `polygon(0 0, ${size}px 0, ${size}px ${cy}px, ${arc.join(', ')}, 0 ${cy}px)`;
}

type Node = Readonly<{ id: string; box: Box; rig: boolean; paint: ReactNode | AgentItem }>;
type Bounds = Readonly<{ left: number; top: number; width: number; height: number }>;

/**
 * Paint order: every item is placed on the lowest plane its depth allows, and each rig sits after
 * the plane of its level. Only what stands in front of a rig and overlaps it rises to a later
 * plane, and those planes are cropped to their contents so the layers the browser composites
 * around the rigs stay small.
 */
function buildLayers(): readonly Layer[] {
  const nodes: Node[] = [];
  const chains: (readonly [string, string])[] = [];
  for (const item of CITY_PLAN.items) {
    if (item.kind === 'agent') {
      nodes.push({ id: item.id, box: item.box, rig: true, paint: item });
    } else if (item.kind === 'trike') {
      const route = { ...item.box, h: 4.6 };
      const seat = { x: TRIKE.x - 0.6, y: TRIKE.start - 0.6, z: 0, w: 1.2, d: 1.2, h: 4 };
      nodes.push({ id: `${item.id}-far`, box: item.box, rig: false, paint: <g key={`${item.id}-far`} data-mover="trike"><Trike x={TRIKE.x} y={TRIKE.start} part="far" /></g> });
      nodes.push({ id: `${item.id}-rider`, box: route, rig: true, paint: { kind: 'agent', id: 'mint', box: seat, colour: 'mint', size: RIG_SIZE, cue: 'deliver' } });
      nodes.push({ id: `${item.id}-near`, box: item.box, rig: false, paint: <g key={`${item.id}-near`} data-mover="trike"><Trike x={TRIKE.x} y={TRIKE.start} part="near" /></g> });
      chains.push([`${item.id}-far`, `${item.id}-rider`], [`${item.id}-rider`, `${item.id}-near`]);
    } else if (item.kind === 'crane') {
      // the head sorts above the mast by height, so the jib passes over the frame it reaches across
      nodes.push({ id: `${item.id}-mast`, box: item.box, rig: false, paint: <CraneMast key={`${item.id}-mast`} box={item.box} /> });
      nodes.push({ id: `${item.id}-head`, box: craneHead(item.box, item.jib), rig: false, paint: <CraneHead key={`${item.id}-head`} box={item.box} reach={item.jib} /> });
      chains.push([`${item.id}-mast`, `${item.id}-head`]);
    } else {
      nodes.push({ id: item.id, box: item.box, rig: false, paint: <Piece key={item.id} item={item} /> });
    }
  }
  // the manhole is painted before the agent climbing out of it, whatever their boxes say
  const dig = CITY_PLAN.items.find((item) => item.kind === 'dig');
  const digger = CITY_PLAN.items.find((item) => item.kind === 'agent' && item.cue === 'uncover');
  if (dig !== undefined && digger !== undefined) chains.push([dig.id, digger.id]);
  const leveled = paintLevels(nodes, chains);
  const top = Math.max(...leveled.map((entry) => entry.level));
  const layers: Layer[] = [];
  for (let level = 0; level <= top; level += 1) {
    const here = leveled.filter((entry) => entry.level === level).map((entry) => entry.node);
    const painted = here.filter((node) => !node.rig);
    layers.push({ kind: 'svg', nodes: painted.map((node) => node.paint as ReactNode), bounds: level === 0 ? FULL : cover(painted.map((node) => node.box)) });
    for (const node of here) if (node.rig) layers.push({ kind: 'agent', item: node.paint as AgentItem, riding: node.id.endsWith('-rider') });
  }
  return layers;
}

const FULL: Bounds = { left: 0, top: 0, width: DESIGN.width, height: DESIGN.height };

/** Where the bubbles can reach: above their points, drifting up and to the right as they float. */
const BUBBLES: Bounds = (() => {
  const points = CITY_PLAN.bubbles.map((bubble) => iso(bubble.at));
  const left = Math.floor(Math.min(...points.map(([x]) => x)) - 40);
  const top = Math.floor(Math.min(...points.map(([, y]) => y)) - 130);
  const right = Math.ceil(Math.max(...points.map(([x]) => x)) + 70);
  const bottom = Math.ceil(Math.max(...points.map(([, y]) => y)) + 20);
  return { left, top, width: right - left, height: bottom - top };
})();

/** The screen area a set of boxes can paint, with room for trees, heads and shadows that overhang their boxes. */
function cover(boxes: readonly Box[]): Bounds {
  if (boxes.length === 0) return { left: 0, top: 0, width: 1, height: 1 };
  const all = boxes.map((box) => screenBounds(box));
  const left = Math.floor(Math.min(...all.map((b) => b.left)) - 40);
  const top = Math.floor(Math.min(...all.map((b) => b.top)) - 110);
  const right = Math.ceil(Math.max(...all.map((b) => b.right)) + 40);
  const bottom = Math.ceil(Math.max(...all.map((b) => b.bottom)) + 24);
  return { left, top, width: right - left, height: bottom - top };
}

/** The city's paint layers never change, so they are built once. */
const LAYERS = buildLayers();

function Piece(props: Readonly<{ item: Exclude<Item, AgentItem | TrikeItem | CraneItem> }>): JSX.Element {
  const { item } = props;
  switch (item.kind) {
    case 'building': return <Building id={item.id} box={item.box} tint={item.tint} left={item.left} right={item.right} roof={item.roof} seed={seedOf(item.id)} />;
    // the street trees sway; bushes, shrubs and grass are too low for it to show
    case 'tree': return <Tree box={item.box} plant={item.plant} size={item.size} flip={item.flip} sway={item.plant === 'round' || item.plant === 'cone'} />;
    case 'lamp': return <Lamp box={item.box} />;
    case 'bench': return <Bench box={item.box} along={item.along} />;
    case 'planter': return <Planter box={item.box} />;
    case 'table': return <Table box={item.box} />;
    case 'frame': return <Frame box={item.box} />;
    case 'load': return <CraneRig />;
    case 'person': return <Person at={iso([item.box.x + item.box.w / 2, item.box.y + item.box.d / 2, 0])} figure={item.figure} flip={item.flip} tempoMs={item.tempoMs} offsetMs={item.offsetMs} cued={item.cued} />;
    case 'dig': return <Dig box={item.box} />;
  }
}

/** The crane's trolley, cable and hanging panel, drawn under the mast at full lift; the clock moves them. */
function CraneRig(): JSX.Element {
  const mx = CRANE_BOX.x + CRANE_BOX.w / 2;
  const my = CRANE_BOX.y + CRANE_BOX.d / 2;
  const [tx, ty] = iso([mx, my, TROLLEY_BOTTOM]);
  const [hx, hy] = iso([mx, my, LOAD_REST_TOP + HOOK_ABOVE]);
  const corners = [iso([mx - 0.65, my, LOAD_REST_TOP]), iso([mx + 0.65, my, LOAD_REST_TOP])];
  return <g>
    <path data-mover="cable" d={openPath([[tx, ty], [hx, hy]])} stroke={INK} strokeWidth={W.fine} />
    <g data-mover="trolley">
      <Solid box={{ x: mx - 0.4, y: my - 0.25, z: TROLLEY_BOTTOM, w: 0.8, d: 0.5, h: 0.3 }} tint="peach" weight={W.fine} />
    </g>
    <g data-mover="load">
      <path d={openPath([corners[0], [hx, hy], corners[1]])} fill="none" stroke={INK} strokeWidth={W.fine} strokeLinejoin="round" />
      <path d={`M${hx} ${hy - 3.5}L${hx} ${hy + 1.5}`} stroke={INK} strokeWidth={W.line} strokeLinecap="round" />
      <CraneLoad box={{ x: mx - 0.75, y: my - 0.2, z: LOAD_REST_TOP - LOAD_HEIGHT, w: 1.5, d: 0.4, h: LOAD_HEIGHT }} />
    </g>
  </g>;
}

function seedOf(id: string): number {
  let hash = 7;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 100000;
}

/**
 * Advances the story clock only while the city is live, so a paused hero resumes where it stopped.
 * Continuous motion (the trike, the crane, the Case File, aqua in the dig) and the stepped loops
 * (`cycles.ts`: flickering windows, people's frames, bar charts) are written straight to
 * the DOM each frame; the scene is React state, since it changes a few times a loop, and the cued
 * customer follows it through its data attribute.
 */
function useCityClock(live: boolean, still: boolean, host: RefObject<HTMLDivElement | null>, mounted: boolean): Scene {
  const start = still ? STILL_MS : 0;
  const elapsed = useRef(start);
  const [scene, setScene] = useState<Scene>(() => sceneAt(start));
  useEffect(() => {
    const root = host.current;
    if (root === null) return;
    for (const person of root.querySelectorAll('[data-cue]')) person.classList.toggle('is-on', scene.thumbs);
  }, [scene, host, mounted]);
  useEffect(() => {
    const root = host.current;
    if (!mounted || root === null) return;
    const find = (name: string): readonly Element[] => {
      const found = [...root.querySelectorAll(`[data-mover="${name}"]`)];
      if (found.length === 0) throw new Error(`The city clock cannot find its ${name}.`);
      return found;
    };
    // most of a loop most movers stand still, so a value is written only when it changes: an
    // unchanged write would still make the browser restyle and repaint the plane it sits in
    const written = new Map<Element, Map<string, string>>();
    const write = (element: Element, key: string, value: string): void => {
      const last = written.get(element) ?? new Map<string, string>();
      written.set(element, last);
      if (last.get(key) === value) return;
      last.set(key, value);
      if (key.startsWith('style:')) (element as HTMLElement | SVGElement).style.setProperty(key.slice(6), value);
      else element.setAttribute(key, value);
    };
    const place = (part: Element, dx: number, dy: number, opacity: number): void => {
      if (part instanceof HTMLElement || part instanceof SVGSVGElement) {
        write(part, 'style:transform', `translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px)`);
        write(part, 'style:opacity', opacity.toFixed(3));
      } else {
        write(part, 'transform', `translate(${dx.toFixed(2)} ${dy.toFixed(2)})`);
        write(part, 'opacity', opacity.toFixed(3));
      }
    };
    const trike = find('trike');
    const [trolley] = find('trolley');
    const [load] = find('load');
    const [cable] = find('cable');
    const [token] = find('case');
    const [built] = find('build');
    const [shadow] = find('case-shadow');
    const [digger] = find('dig');
    const cycles = [...root.querySelectorAll('[data-cycle]')].map((element) => ({ element, cycle: readCycle(element) }));
    const apply = (ms: number): void => {
      for (const part of cycles) write(part.element, 'opacity', shownAt(part.cycle, ms) ? '1' : '0');
      const ride = trikeAt(ms, TRIKE.distance);
      const [dx, dy] = isoDelta(0, -ride.travelled, 0);
      for (const part of trike) place(part, dx, dy, ride.opacity);
      const hook = craneAt(ms);
      const [jx, jy] = isoDelta(hook.reach, 0, 0);
      const sink = (1 - hook.lift) * LOAD_TRAVEL * 20;
      const [tx, ty] = iso([CRANE_BOX.x + CRANE_BOX.w / 2 + hook.reach, CRANE_BOX.y + CRANE_BOX.d / 2, TROLLEY_BOTTOM]);
      const [, hy] = iso([0, 0, LOAD_REST_TOP + HOOK_ABOVE]);
      const [, trolleyY] = iso([0, 0, TROLLEY_BOTTOM]);
      write(trolley, 'transform', `translate(${jx.toFixed(2)} ${jy.toFixed(2)})`);
      write(load, 'transform', `translate(${jx.toFixed(2)} ${(jy + sink).toFixed(2)})`);
      write(cable, 'd', openPath([[tx, ty], [tx, ty + (hy - trolleyY) + sink]]));
      const carried = caseAt(ms);
      const [fx, fy] = iso(carried.at);
      const [gx, gy] = iso([carried.at[0], carried.at[1], 0]);
      // on the interview table the Case File lies at table size, still, with no shadow of its own
      write(token, 'style:transform', `translate(${fx.toFixed(2)}px, ${fy.toFixed(2)}px) scale(${(1 - CASE_REST_SHRINK * carried.rest).toFixed(3)})`);
      write(token, 'style:opacity', carried.file.toFixed(3));
      write(token, 'data-rest', carried.rest > 0.5 ? 'yes' : 'no');
      place(built, fx, fy, carried.build);
      place(shadow, gx, gy, Math.max(carried.file, carried.build) * 0.9 * (1 - carried.rest));
      write(digger, 'style:transform', `translateY(${(HOLE.sunk + (HOLE.risen - HOLE.sunk) * digAt(ms)).toFixed(2)}px)`);
      const next = sceneAt(ms);
      setScene((previous) => (sameScene(previous, next) ? previous : next));
    };
    apply(elapsed.current);
    if (!live) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number): void => {
      elapsed.current += Math.min(100, Math.max(0, now - last));
      last = now;
      apply(elapsed.current);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [live, host, mounted]);
  return scene;
}
