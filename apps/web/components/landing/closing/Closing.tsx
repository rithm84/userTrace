"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type CSSProperties, type JSX, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

import { useReducedMotion } from "@/components/agent/useReducedMotion";

import { Waitlist } from "../Waitlist";
import { AGENT_HEX, type AgentColour } from "../characters/AgentSprite";
import { Cameo, ENTRANCE_MS } from "../pieces";
import "./closing.css";

type Point = Readonly<{ x: number; y: number }>;
type Gaze = readonly [number, number];
/** pulled: the six have dragged the glass right; pulling: the reader has the handle, or asked for it from the keyboard; settled: the glass is in its spot and stays there. */
type Phase = "pulled" | "pulling" | "settled";

/** The design box the composition is laid out in (CSS px); the section centres it and clips the rest. */
const STAGE = Object.freeze({ width: 1180, height: 800 });
/** Radius of the clear glass: the circle that clips the magnified copy. */
const LENS_R = 300;
/** How much the copy is magnified about the lens centre; the rim hides the seam. */
const LENS_K = 1.25;
/** The barrel bow: each letter is pushed out from the lens centre by this fraction of its offset, fading to nothing at the rim. */
const LENS_BOW = 0.1;
/** The spot: the lens centre where the whole headline reads through the glass. */
const REST: Point = Object.freeze({ x: 610, y: 396 });
/** How far right the six have dragged the glass when the reader arrives. */
const PULL = 220;
const PULLED: Point = Object.freeze({ x: REST.x + PULL, y: REST.y });
/** Within this of the spot the glass clicks into place. */
const SNAP = 18;
/** The six resist: the glass gives this much of the hand's travel along the handle; they take ground back at this many px a second once the hand has rested this long, and a quarter of that while it moves. */
const PULL_RATIO = 0.62;
const CREEP_PX_PER_S = 40;
const CREEP_WHILE_MOVING = 0.25;
const HAND_REST_MS = 90;
/** The pull is along the handle: sideways pointer travel moves the glass by this fraction, at most this far. */
const GIVE = Object.freeze({ fraction: 0.25, max: 16 });
/** Coral's body follows its glass by this fraction of the lens travel; the hands ride the handle. */
const CORAL_FOLLOW = Object.freeze({ x: 0.7, y: 0.35 });
/** The headline's base box leaves room for the email bar, centring both inside the settled lens. */
const HEADLINE = Object.freeze({ left: REST.x - 300, top: REST.y - 136, width: 600, fontSize: 64 });
const HEADLINE_LINES: readonly string[] = Object.freeze(["Give good", "feedback", "a way forward."]);
/** The drawn glass (rim, handle, hatching; the lens is alpha), a 2x raster: the clear circle and the handle in image px. */
const GLASS = Object.freeze({ src: "/landing/closing/glass.webp", width: 2002, height: 2008, centre: { x: 1222, y: 820.6 }, radius: 660.6, collar: { x: 622, y: 1396 }, tip: { x: 48, y: 1956 } });
const GLASS_SCALE = LENS_R / GLASS.radius;
/** The drawn glass turns clockwise about the lens centre by this much, so the handle runs out to the left along the lower rim instead of down into the footer; the rim is round, so the clear circle stays where it is. */
const GLASS_TILT_DEG = 30;
/** Inline style lengths are rounded to what the browser reads back, so the server's HTML and the client's props agree at hydration. */
const px = (value: number): number => Math.round(value * 100) / 100;
/** Where the glass image sits with the lens at the spot, so its clear circle lands there. */
const GLASS_AT_REST: Point = Object.freeze({ x: px(REST.x - GLASS.centre.x * GLASS_SCALE), y: px(REST.y - GLASS.centre.y * GLASS_SCALE) });
/** The glass image's box and the clear circle's centre in it, in CSS px. */
const GLASS_BOX = Object.freeze({ width: px(GLASS.width * GLASS_SCALE), height: px(GLASS.height * GLASS_SCALE), centre: { x: px(GLASS.centre.x * GLASS_SCALE), y: px(GLASS.centre.y * GLASS_SCALE) } });
/** Coral's hands, as fractions of the way down the handle from the collar to the tip. */
const HANDS: readonly number[] = Object.freeze([0.2, 0.55]);
const HAND_SIZE = 60;
/** The reader's grip: a hit box this wide laid along the handle's axis from the collar to the tip. */
const GRIP_WIDTH = 72;
/** Coral's body: the fourteen-state rig at this box, whose body fills 0.6 of it centred at (0.5, 0.54); it sits at the end of the handle so its hands hang onto it. */
const CORAL_SIZE = 430;
const CORAL_BODY: Point = Object.freeze({ x: 82, y: 375 });
const CAST: readonly AgentColour[] = Object.freeze(["black", "mint", "sky", "lilac", "peach", "aqua"]);
const CAST_SIZE = 140;
/** Where each of the six hangs on the rim: degrees around the lens centre, three o'clock is 0 and up is negative. */
const CAST_ANGLES_DEG: readonly number[] = Object.freeze([-50, -30, -10, 10, 30, 50]);
/** Body centres this far from the lens centre, on the outer edge of the drawn rim; each pair of hands on the ring, either side of the body's angle. */
const CAST_RADIUS = 372;
const CAST_HAND_RADIUS = 330;
const CAST_HAND_SPREAD_DEG = 6.5;
const CAST_HAND_SIZE = 24;
/** The cue on the left, in stage px: a sketched arrow 42px under the handle's lower edge at arrival (which falls 0.246px per px leftward from y 550 at x 400), and a caption under it. */
const CUE = Object.freeze({ left: 130, top: 572, width: 300, height: 110, caption: { left: 28, top: 120 } });
const GAZE_REACH = 420;
/** The glass rides a damped spring: soft when the six take it back, stiffer under the hand so it lags with weight rather than wallows. */
const SPRING_OMEGA = 2 * Math.PI * 1.1;
const DRAG_OMEGA = 2 * Math.PI * 2;
const SPRING_ZETA = 0.82;

function rigBox(centre: Point, size: number): Point {
  return { x: px(centre.x - size / 2), y: px(centre.y - size * 0.54) };
}

/** A point `radius` from the settled lens centre at `deg` around it. */
function onRim(deg: number, radius: number): Point {
  const a = (deg * Math.PI) / 180;
  return { x: px(REST.x + radius * Math.cos(a)), y: px(REST.y + radius * Math.sin(a)) };
}

function castBody(index: number): Point {
  return onRim(CAST_ANGLES_DEG[index], CAST_RADIUS);
}

function castHands(index: number): readonly Point[] {
  return [-1, 1].map((side) => onRim(CAST_ANGLES_DEG[index] + side * CAST_HAND_SPREAD_DEG, CAST_HAND_RADIUS));
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function gazeToward(from: Point, to: Point): Gaze {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const k = Math.min(1, len / GAZE_REACH);
  return [Math.round((dx / len) * k * 100) / 100, Math.round((dy / len) * k * 100) / 100];
}

function coralBodyAt(lens: Point): Point {
  return { x: CORAL_BODY.x + CORAL_FOLLOW.x * (lens.x - REST.x), y: CORAL_BODY.y + CORAL_FOLLOW.y * (lens.y - REST.y) };
}

type Looks = Readonly<{ coral: Gaze; cast: readonly Gaze[] }>;

/** Where every character looks: coral at its glass; the six at the rim they are pulling on, and once the glass is settled, at the pointer. */
function looksAt(lens: Point, pointer: Point | null, settled: boolean): Looks {
  const cast = CAST.map((_, index) => {
    const body = { x: castBody(index).x + lens.x - REST.x, y: castBody(index).y + lens.y - REST.y };
    return gazeToward(body, settled ? (pointer ?? lens) : lens);
  });
  return { coral: gazeToward(coralBodyAt(lens), lens), cast };
}

function sameLooks(a: Looks, b: Looks): boolean {
  return a.coral[0] === b.coral[0] && a.coral[1] === b.coral[1] && a.cast.every((gaze, index) => gaze[0] === b.cast[index][0] && gaze[1] === b.cast[index][1]);
}

const translate = (x: number, y: number): string => `translate3d(${x}px, ${y}px, 0)`;
const lensTransform = (lens: Point): string => translate(lens.x - LENS_R, lens.y - LENS_R);
const copyTransform = (lens: Point): string => translate(LENS_R + LENS_K * (HEADLINE.left - lens.x), LENS_R + LENS_K * (HEADLINE.top - lens.y));
const glassTransform = (lens: Point): string => `${translate(lens.x - REST.x, lens.y - REST.y)} rotate(${GLASS_TILT_DEG}deg)`;
const coralTransform = (lens: Point): string => translate(CORAL_FOLLOW.x * (lens.x - REST.x), CORAL_FOLLOW.y * (lens.y - REST.y));
const castTransform = (lens: Point): string => translate(lens.x - REST.x, lens.y - REST.y);

type Letter = Readonly<{ el: HTMLSpanElement; x: number; y: number }>;

/** Each letter of the copy and its centre in the copy's own box, measured with the bow at zero. */
function measureLetters(copy: HTMLElement): readonly Letter[] {
  const elements = [...copy.querySelectorAll<HTMLSpanElement>("[data-letter]")];
  for (const el of elements) {
    el.style.left = "0px";
    el.style.top = "0px";
  }
  const box = copy.getBoundingClientRect();
  return elements.map((el) => {
    const rect = el.getBoundingClientRect();
    return { el, x: rect.left + rect.width / 2 - box.left, y: rect.top + rect.height / 2 - box.top };
  });
}

/** Bow the copy's letters about the lens centre: outward, most a third of the way out, none at the rim. */
function bowLetters(letters: readonly Letter[], lens: Point): void {
  const originX = LENS_K * (HEADLINE.left - lens.x);
  const originY = LENS_K * (HEADLINE.top - lens.y);
  for (const letter of letters) {
    const qx = originX + letter.x;
    const qy = originY + letter.y;
    const push = LENS_BOW * Math.max(0, 1 - (qx * qx + qy * qy) / (LENS_R * LENS_R));
    letter.el.style.left = `${(qx * push).toFixed(2)}px`;
    letter.el.style.top = `${(qy * push).toFixed(2)}px`;
  }
}

/** The parts that move with the glass. */
type StageParts = { lens: HTMLDivElement | null; copy: HTMLDivElement | null; glass: HTMLDivElement | null; coral: HTMLDivElement | null; cast: HTMLDivElement | null; castHands: HTMLDivElement | null };

/** Put the glass, the copy, coral, the six and their hands where a lens centre of `lens` needs them. */
function placeAt(parts: StageParts, letters: readonly Letter[], lens: Point): void {
  const { lens: lensEl, copy, glass, coral, cast, castHands } = parts;
  if (lensEl === null || copy === null || glass === null || coral === null || cast === null || castHands === null) {
    throw new Error("The closing cannot place its glass before its stage has rendered.");
  }
  lensEl.style.transform = lensTransform(lens);
  copy.style.transform = copyTransform(lens);
  glass.style.transform = glassTransform(lens);
  coral.style.transform = coralTransform(lens);
  cast.style.transform = castTransform(lens);
  castHands.style.transform = castTransform(lens);
  bowLetters(letters, lens);
}

/** One hand on the handle: where it took hold, where it is now and when it last moved, and the ground the glass held then (the six move that as they creep it back). */
type Drag = { pointerId: number; x: number; y: number; lastX: number; lastY: number; movedAt: number; lensX: number };

type Motion = {
  lens: { x: number; y: number; vx: number; vy: number };
  target: Point;
  pointer: Point | null;
  drag: Drag | null;
  frame: number;
  last: number;
};

/**
 * The closing: a tug of war over the drawn magnifying glass. Coral hangs onto the handle on the
 * left; the other six hang off the right of the rim and have dragged the glass their way, so
 * only the end of the headline shows through it. The headline exists only through the glass: the
 * page copy is transparent and a circle of paper carries a copy set 1.25 times larger, offset so
 * it registers with the type beneath, each letter pushed out from the lens centre so the lines
 * bow. A sketched arrow and a caption on the left say what to do. The reader grabs the handle
 * (a real button along it) and pulls. The six resist: the glass gives only part of the hand's
 * travel, lags it on a spring, and creeps back toward them whenever the hand rests, so the reader
 * has to keep pulling. Within a few pixels of the spot it clicks into place, the buttons appear,
 * and the glass stays put. Let go early and the six win it back. Enter or Space on the handle
 * pulls it in for the keyboard, unresisted. With reduced motion the glass is already in its spot
 * and every rig parks after its entrance.
 */
export function Closing(): JSX.Element {
  const reducedMotion = useReducedMotion();
  const stage = useRef<HTMLDivElement | null>(null);
  const lensEl = useRef<HTMLDivElement | null>(null);
  const copyEl = useRef<HTMLDivElement | null>(null);
  const glassEl = useRef<HTMLDivElement | null>(null);
  const coralEl = useRef<HTMLDivElement | null>(null);
  const castEl = useRef<HTMLDivElement | null>(null);
  const castHandsEl = useRef<HTMLDivElement | null>(null);
  const actionsEl = useRef<HTMLDivElement | null>(null);
  const motion = useRef<Motion>({ lens: { x: PULLED.x, y: PULLED.y, vx: 0, vy: 0 }, target: PULLED, pointer: null, drag: null, frame: 0, last: 0 });
  const letters = useRef<readonly Letter[]>([]);
  const [phase, setPhase] = useState<Phase>("pulled");
  const [failedPulls, setFailedPulls] = useState(0);
  const phaseRef = useRef<Phase>("pulled");
  const focusOnSettle = useRef(false);
  const [looks, setLooks] = useState<Looks>(() => looksAt(PULLED, null, false));
  const shown = useRef(looks);
  const settled = reducedMotion || phase === "settled";
  /** Where the glass rests before the reader touches it: in its spot when motion is reduced, otherwise pulled right. Only the reduced-motion answer ever changes it, so React never rewrites a transform mid-pull. */
  const home = reducedMotion ? REST : PULLED;

  useEffect(() => () => window.cancelAnimationFrame(motion.current.frame), []);

  // the letters are measured once the display face is in, then bowed where the glass is
  useEffect(() => {
    let cancelled = false;
    void document.fonts.ready.then(() => {
      if (cancelled || copyEl.current === null) return;
      letters.current = measureLetters(copyEl.current);
      bowLetters(letters.current, { x: motion.current.lens.x, y: motion.current.lens.y });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // the reduced-motion answer arrives after the first paint; an unsettled glass moves to where that answer puts it
  useEffect(() => {
    if (phaseRef.current === "settled") return;
    const m = motion.current;
    window.cancelAnimationFrame(m.frame);
    m.frame = 0;
    m.last = 0;
    m.drag = null;
    m.lens = { x: home.x, y: home.y, vx: 0, vy: 0 };
    m.target = home;
    placeAt({ lens: lensEl.current, copy: copyEl.current, glass: glassEl.current, coral: coralEl.current, cast: castEl.current, castHands: castHandsEl.current }, letters.current, home);
  }, [home]);

  // a keyboard pull lands focus on the waitlist field or confirmation once it exists
  useEffect(() => {
    if (phase !== "settled" || !focusOnSettle.current) return;
    focusOnSettle.current = false;
    actionsEl.current?.querySelector<HTMLElement>("[data-waitlist-target]")?.focus({ preventScroll: true });
  }, [phase]);

  const stageParts = (): StageParts => ({ lens: lensEl.current, copy: copyEl.current, glass: glassEl.current, coral: coralEl.current, cast: castEl.current, castHands: castHandsEl.current });

  const setPhaseNow = (next: Phase): void => {
    phaseRef.current = next;
    setPhase(next);
  };

  const look = (lens: Point, pointer: Point | null): void => {
    const next = looksAt(lens, pointer, phaseRef.current === "settled");
    if (sameLooks(next, shown.current)) return;
    shown.current = next;
    setLooks(next);
  };

  const stop = (): void => {
    const m = motion.current;
    window.cancelAnimationFrame(m.frame);
    m.frame = 0;
    m.last = 0;
  };

  const settle = (): void => {
    stop();
    const m = motion.current;
    m.lens = { x: REST.x, y: REST.y, vx: 0, vy: 0 };
    m.target = REST;
    m.drag = null;
    setPhaseNow("settled");
    placeAt(stageParts(), letters.current, REST);
    look(REST, m.pointer);
  };

  const tick = (now: number): void => {
    const m = motion.current;
    const dt = m.last === 0 ? 1 / 60 : Math.min(0.05, (now - m.last) / 1000);
    m.last = now;
    if (m.drag !== null) {
      // the six take ground back, in earnest once the hand rests, until they have all of it; the hand's travel along the handle wins some of it
      const pull = (m.drag.lastX - m.drag.x) * PULL_RATIO;
      const creep = CREEP_PX_PER_S * (now - m.drag.movedAt > HAND_REST_MS ? 1 : CREEP_WHILE_MOVING) * dt;
      m.drag.lensX = Math.min(PULLED.x - pull, m.drag.lensX + creep);
      m.target = {
        x: clamp(m.drag.lensX + pull, REST.x, PULLED.x),
        y: REST.y + clamp((m.drag.lastY - m.drag.y) * GIVE.fraction, -GIVE.max, GIVE.max),
      };
    }
    const omega = m.drag === null ? SPRING_OMEGA : DRAG_OMEGA;
    for (const axis of ["x", "y"] as const) {
      const v = axis === "x" ? "vx" : "vy";
      const acceleration = omega * omega * (m.target[axis] - m.lens[axis]) - 2 * SPRING_ZETA * omega * m.lens[v];
      m.lens[v] += acceleration * dt;
      m.lens[axis] += m.lens[v] * dt;
    }
    if (phaseRef.current === "pulling" && Math.hypot(m.lens.x - REST.x, m.lens.y - REST.y) <= SNAP) {
      settle();
      return;
    }
    const still = m.drag === null && Math.abs(m.lens.vx) < 0.5 && Math.abs(m.lens.vy) < 0.5 && Math.abs(m.target.x - m.lens.x) < 0.1 && Math.abs(m.target.y - m.lens.y) < 0.1;
    if (still) {
      m.lens.x = m.target.x;
      m.lens.y = m.target.y;
      m.lens.vx = 0;
      m.lens.vy = 0;
    }
    const lens = { x: m.lens.x, y: m.lens.y };
    placeAt(stageParts(), letters.current, lens);
    look(lens, m.pointer);
    if (still) {
      m.frame = 0;
      m.last = 0;
      return;
    }
    m.frame = window.requestAnimationFrame(tick);
  };

  const wake = (): void => {
    if (motion.current.frame === 0) motion.current.frame = window.requestAnimationFrame(tick);
  };

  const onGripDown = (event: ReactPointerEvent<HTMLButtonElement>): void => {
    if (phaseRef.current === "settled") return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    stop();
    const m = motion.current;
    m.drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY, movedAt: performance.now(), lensX: m.lens.x };
    setPhaseNow("pulling");
    wake();
  };

  // the hand moves; the tug itself is worked out frame by frame in the loop
  const onGripMove = (event: ReactPointerEvent<HTMLButtonElement>): void => {
    const drag = motion.current.drag;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    drag.lastX = event.clientX;
    drag.lastY = event.clientY;
    drag.movedAt = performance.now();
    wake();
  };

  // the six win back a glass that is let go before the spot
  const onGripUp = (event: ReactPointerEvent<HTMLButtonElement>): void => {
    const m = motion.current;
    if (m.drag === null || m.drag.pointerId !== event.pointerId) return;
    m.drag = null;
    m.target = PULLED;
    setPhaseNow("pulled");
    setFailedPulls((count) => count + 1);
    wake();
  };

  const onGripKey = (event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    if (phaseRef.current === "settled") return;
    stop();
    motion.current.drag = null;
    motion.current.target = REST;
    focusOnSettle.current = true;
    setPhaseNow("pulling");
    wake();
  };

  const onStageMove = (event: ReactPointerEvent<HTMLElement>): void => {
    if (stage.current === null) return;
    const rect = stage.current.getBoundingClientRect();
    const m = motion.current;
    m.pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    if (m.frame === 0 && m.drag === null) look({ x: m.lens.x, y: m.lens.y }, m.pointer);
  };

  const onStageLeave = (): void => {
    const m = motion.current;
    m.pointer = null;
    if (m.frame === 0 && m.drag === null) look({ x: m.lens.x, y: m.lens.y }, null);
  };

  const tracking = !reducedMotion;
  const park = reducedMotion ? ENTRANCE_MS : null;
  const pose = settled ? "idle" : "concern";
  const coralBox = rigBox(CORAL_BODY, CORAL_SIZE);
  return (
    <section
      className="closing-section"
      aria-labelledby="closing-title"
      data-testid="landing-closing"
      data-reduced-motion={reducedMotion}
      data-phase={settled ? "settled" : phase}
      id="book-demo"
      onPointerMove={tracking ? onStageMove : undefined}
      onPointerLeave={tracking ? onStageLeave : undefined}
    >
      <div ref={stage} className="lp-wrap closing-stage" data-testid="closing-stage" style={{ height: STAGE.height }}>
        {/* the page's own headline is transparent: it is read through the glass, and by readers and robots from the DOM */}
        <h2 className="lp-h2 closing-headline closing-headline-base" id="closing-title" style={{ left: HEADLINE.left, top: HEADLINE.top, width: HEADLINE.width, fontSize: HEADLINE.fontSize, zIndex: 1 }}>
          <Lines />
        </h2>

        {/* coral, large, hanging onto the handle; its eyes stay on the glass */}
        <div ref={coralEl} className="closing-character" style={{ left: coralBox.x, top: coralBox.y, width: CORAL_SIZE, height: CORAL_SIZE, zIndex: 2, transform: coralTransform(home) }}>
          <Cameo colour="coral" state={pose} park={park} size={CORAL_SIZE} gazeX={looks.coral[0]} gazeY={looks.coral[1]} testId="closing-coral" style={{ left: 0, top: 0 }} />
        </div>

        {/* everything that rides the glass; while the six are winning, it is tugged on twos */}
        <div className={"closing-assembly" + (settled ? "" : " is-tugging")} data-testid="closing-assembly" style={{ zIndex: 3 }}>
          {/* the clear glass: paper and the magnified copy, clipped to the lens circle */}
          <div ref={lensEl} className="closing-lens" data-testid="closing-lens" style={{ width: LENS_R * 2, height: LENS_R * 2, zIndex: 1, transform: lensTransform(home) }}>
            <div ref={copyEl} aria-hidden="true" className="lp-h2 closing-headline closing-lens-copy" data-testid="closing-lens-copy" style={{ width: HEADLINE.width * LENS_K, fontSize: HEADLINE.fontSize * LENS_K, transform: copyTransform(home) }}>
              <Lines letters />
            </div>
          </div>

          {/* the six behind the rim, spaced along its right-hand curve */}
          <div ref={castEl} className="closing-cast" data-testid="closing-cast" style={{ zIndex: 2, transform: castTransform(home) }}>
            {CAST.map((colour, index) => {
              const box = rigBox(castBody(index), CAST_SIZE);
              return (
                <div key={colour} className="closing-character" style={{ left: box.x, top: box.y, width: CAST_SIZE, height: CAST_SIZE }}>
                  <Cameo colour={colour} state={pose} park={park} size={CAST_SIZE} gazeX={looks.cast[index][0]} gazeY={looks.cast[index][1]} testId={`closing-cast-${colour}`} style={{ left: 0, top: 0 }} />
                </div>
              );
            })}
          </div>

          {/* the drawn glass, coral's hands and the reader's grip ride the lens, turned about its centre */}
          <div ref={glassEl} className="closing-glass" data-testid="closing-glass" style={{ left: GLASS_AT_REST.x, top: GLASS_AT_REST.y, width: GLASS_BOX.width, height: GLASS_BOX.height, zIndex: 3, transformOrigin: `${GLASS_BOX.centre.x}px ${GLASS_BOX.centre.y}px`, transform: glassTransform(home) }}>
            <Image src={GLASS.src} alt="" width={GLASS.width} height={GLASS.height} sizes="1024px" quality={90} draggable={false} style={{ width: GLASS_BOX.width, height: GLASS_BOX.height }} />
            {HANDS.map((t) => (
              <span key={t} aria-hidden="true" className="closing-hand" style={handStyle(t)} />
            ))}
            {settled ? null : (
              <button
                type="button"
                className="closing-grip"
                data-testid="closing-grip"
                aria-label="Pull the magnifying glass into place"
                style={gripStyle()}
                onPointerDown={onGripDown}
                onPointerMove={onGripMove}
                onPointerUp={onGripUp}
                onPointerCancel={onGripUp}
                onLostPointerCapture={onGripUp}
                onKeyDown={onGripKey}
              />
            )}
          </div>

          {/* the six's hands, over the ring */}
          <div ref={castHandsEl} className="closing-cast" data-testid="closing-cast-hands" style={{ zIndex: 4, transform: castTransform(home) }}>
            {CAST.map((colour, index) =>
              castHands(index).map((hand, side) => (
                <span key={`${colour}-${side}`} aria-hidden="true" className="closing-cast-hand" style={{ left: hand.x - CAST_HAND_SIZE / 2, top: hand.y - CAST_HAND_SIZE / 2, width: CAST_HAND_SIZE, height: CAST_HAND_SIZE, background: AGENT_HEX[colour] }} />
              )),
            )}
          </div>
        </div>

        <div ref={actionsEl} className="closing-actions" data-testid="closing-actions" data-shown={settled} inert={!settled} style={{ left: REST.x - 215, width: 430, top: HEADLINE.top + HEADLINE.fontSize * 3 + 36, zIndex: 4 }}>
          <Waitlist id="closing" />
        </div>

        {phase === "pulled" && !settled ? <Cue failedPulls={failedPulls} /> : null}
      </div>
    </section>
  );
}

/** The headline's lines; with `letters`, every character is its own span so the lens can bow it. */
function Lines(props: Readonly<{ letters?: boolean }>): JSX.Element {
  return (
    <>
      {HEADLINE_LINES.map((line, index) => (
        <span key={line}>
          {index > 0 ? <br /> : null}
          {props.letters === true
            ? [...line].map((character, position) => (
                <span key={position} className="closing-lens-letter" data-letter="">
                  {character}
                </span>
              ))
            : line}
        </span>
      ))}
    </>
  );
}

/** The cue: a twice-drawn arrow running left along the handle, nudging on twos, and a caption under it. */
function Cue(props: Readonly<{ failedPulls: number }>): JSX.Element {
  return (
    <div className="closing-cue" data-testid="closing-cue" style={{ left: CUE.left, top: CUE.top, width: CUE.width, height: CUE.height + 60, zIndex: 5 }}>
      <svg className="closing-cue-arrow" width={CUE.width} height={CUE.height} viewBox={`0 0 ${CUE.width} ${CUE.height}`} aria-hidden="true" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
        <path d="M 274 20 C 200 42, 110 66, 36 78" strokeWidth={3.5} />
        <path d="M 276 25 C 204 47, 116 70, 43 82" strokeWidth={2} opacity={0.8} />
        <path d="M 59 89 L 36 78 L 55 60" strokeWidth={3.5} />
        <path d="M 290 21 l 14 -3 M 288 33 l 15 2" strokeWidth={2.5} opacity={0.8} />
      </svg>
      <span className="closing-cue-caption" style={{ left: CUE.caption.left, top: CUE.caption.top }}>
        {props.failedPulls === 0 ? "Grab the handle and pull." : props.failedPulls === 1 ? "That all you got?" : "Pull harder!"}
      </span>
    </div>
  );
}

/** A hand centred on the handle's axis, `t` of the way from the collar to the tip, in glass-box px. */
function handStyle(t: number): CSSProperties {
  const x = (GLASS.collar.x + (GLASS.tip.x - GLASS.collar.x) * t) * GLASS_SCALE;
  const y = (GLASS.collar.y + (GLASS.tip.y - GLASS.collar.y) * t) * GLASS_SCALE;
  return { left: px(x - HAND_SIZE / 2), top: px(y - HAND_SIZE / 2), width: HAND_SIZE, height: HAND_SIZE };
}

/** The grip's box, centred on the handle and turned to lie along it, in glass-box px. */
function gripStyle(): CSSProperties {
  const collar = { x: GLASS.collar.x * GLASS_SCALE, y: GLASS.collar.y * GLASS_SCALE };
  const tip = { x: GLASS.tip.x * GLASS_SCALE, y: GLASS.tip.y * GLASS_SCALE };
  const length = Math.hypot(tip.x - collar.x, tip.y - collar.y);
  const angle = (Math.atan2(tip.y - collar.y, tip.x - collar.x) * 180) / Math.PI;
  return { left: px((collar.x + tip.x) / 2 - length / 2), top: px((collar.y + tip.y) / 2 - GRIP_WIDTH / 2), width: px(length), height: GRIP_WIDTH, transform: `rotate(${angle.toFixed(2)}deg)` };
}
