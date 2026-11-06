/** The case board: three customer situations as comic panels, one open and playing, two waiting as thumbnails. */
"use client";

import { Check, Pause, Play } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type JSX, type KeyboardEvent } from "react";

import { useReducedMotion } from "@/components/agent/useReducedMotion";

import { Badge } from "../art";
import { Cameo, ENTRANCE_MS } from "../pieces";
import { CASE_TIMES, STEP_STARTS, STEP_STILLS, ramp, stepAt, useCaseClock } from "./caseClock";
import { HELPS_PAGE, OPEN_QUAD, quadBox, quadClip, quadPoints, thumbQuad } from "./helpsGeometry";
import { HelpsExample, SITUATIONS, stickerStateAt, type SituationId } from "./helpsExamples";
import "../story/story.css";
import "./helps.css";

const THUMB_SLOTS = [0, 1] as const;
/** The open case plays while at least this much of the board is on screen. */
const ON_SCREEN = 0.5;

/** How far through step `index` the case is at `t`, for the current step's coral ring. */
function stepFill(index: number, t: number): number {
  const end = STEP_STARTS[index + 1] ?? CASE_TIMES.done;
  return ramp(t, STEP_STARTS[index], end);
}

/**
 * Where uTrace helps. The open case plays on its own clock, like the demo above it: the problem,
 * uTrace on it, the build, and the fixed version, while its specialist moves from the problem to
 * building to done and the case's rail fills. It plays while the board is on screen, pauses when
 * it leaves or when the reader pauses it, and starts over after the fixed version has held. Each
 * stop on the rail jumps to its step; under reduced motion nothing plays, and each stop shows its
 * step's still.
 */
export function WhereItHelps(): JSX.Element {
  const [openId, setOpenId] = useState<SituationId>(SITUATIONS[0].id);
  const [visible, setVisible] = useState(false);
  const [paused, setPaused] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const page = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  const thumbRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusSlot = useRef<number | null>(null);
  const reducedMotion = useReducedMotion();
  const running = visible && !paused && !reducedMotion;
  const { t, seek } = useCaseClock(running, STEP_STILLS[0]);
  const open = SITUATIONS.find((situation) => situation.id === openId);
  if (open === undefined) throw new Error(`Unknown situation ${openId}.`);
  const openIndex = SITUATIONS.indexOf(open);
  const thumbs = SITUATIONS.filter((situation) => situation.id !== openId);
  const step = stepAt(t);

  // reduced motion resolves after the first render: a still for it, the start of the case otherwise
  useEffect(() => {
    seek(reducedMotion ? STEP_STILLS[0] : 0);
  }, [reducedMotion, seek]);

  // the board is a fixed page fitted to the width with CSS zoom, as the demo is, so a narrow window shows the same board, smaller
  useLayoutEffect(() => {
    const element = wrap.current;
    if (element === null) throw new Error("Cannot fit the case board: its wrap is missing.");
    const measure = (): void => {
      // a wrap with no width is not laid out yet
      if (element.clientWidth === 0) return;
      setZoom(Math.min(1, element.clientWidth / HELPS_PAGE.width));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = page.current;
    if (element === null) throw new Error("Cannot watch the case board: its page is missing.");
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.intersectionRatio >= ON_SCREEN), { threshold: [0, ON_SCREEN, 1] });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // a thumbnail that was just opened is gone from the row, so focus stays in its slot
  useEffect(() => {
    if (focusSlot.current === null) return;
    thumbRefs.current[focusSlot.current]?.focus();
    focusSlot.current = null;
  }, [openId]);

  function openCase(id: SituationId, slot: number): void {
    focusSlot.current = slot;
    setOpenId(id);
    seek(reducedMotion ? STEP_STILLS[0] : 0);
  }

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, slot: number): void {
    let next: number;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowRight":
        next = (slot + 1) % THUMB_SLOTS.length;
        break;
      case "ArrowUp":
      case "ArrowLeft":
        next = (slot + THUMB_SLOTS.length - 1) % THUMB_SLOTS.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = THUMB_SLOTS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    thumbRefs.current[next]?.focus();
  }

  return (
    <section className="helps-section" aria-labelledby="helps-title" data-testid="landing-helps" data-reduced-motion={reducedMotion} id="where-it-helps">
      <div ref={wrap} className="helps-wrap">
        <p className="lp-kicker helps-kicker">Where uTrace helps</p>
        <h2 className="lp-h2 helps-title" id="helps-title">
          From product failures
          <br />
          to missing capabilities.
        </h2>
        <p className="lp-lede helps-lede">Sometimes something breaks. Sometimes a customer wants something new. Sometimes they just work around it, every week, without telling anyone. uTrace takes each one to the real problem.</p>

        <div className="hp-fit" style={{ width: HELPS_PAGE.width * (zoom ?? 1), height: HELPS_PAGE.height * (zoom ?? 1) }}>
          <div ref={page} className="hp-page" data-testid="helps-page" data-open={openId} data-playing={running ? "true" : "false"} style={{ width: HELPS_PAGE.width, height: HELPS_PAGE.height, zoom: zoom ?? 1 }}>
            <div className="hp-open" role="region" aria-label={`Case ${openIndex + 1}: ${open.label}`} style={{ ...quadBox(OPEN_QUAD), clipPath: quadClip(OPEN_QUAD) }}>
              <div key={open.id} className="hp-open-body">
                <span className="hp-caption">
                  <i>0{openIndex + 1}</i>
                  <span>{open.label}</span>
                </span>
                {reducedMotion ? null : (
                  <button type="button" className="hp-play" data-testid="helps-play" aria-label={paused ? "Play the case" : "Pause the case"} onClick={() => setPaused((value) => !value)}>
                    {paused ? <Play size={14} strokeWidth={2.6} aria-hidden="true" /> : <Pause size={14} strokeWidth={2.6} aria-hidden="true" />}
                  </button>
                )}
                <div className="hp-crop" data-testid="helps-example" data-situation={open.id}>
                  <HelpsExample situation={open} t={t} />
                </div>
                <ol className="hp-steps" aria-label="Where the case stands" data-testid="helps-steps" data-step={step}>
                  {open.steps.map((label, index) => (
                    <li key={label} className={(index < step ? "is-done" : index === step ? "is-current" : "") + (index <= step ? " is-reached" : "")} data-testid={`helps-step-${index}`} data-status={index < step ? "complete" : index === step ? "current" : "upcoming"}>
                      <button
                        type="button"
                        className="hp-step"
                        aria-current={index === step ? "step" : undefined}
                        onClick={() => seek(reducedMotion ? STEP_STILLS[index] : STEP_STARTS[index])}
                        style={{ "--fill": index === step ? stepFill(index, t) : 0 } as CSSProperties}
                      >
                        <span className="hp-step-dot" aria-hidden="true">
                          {index < step ? <Check size={13} strokeWidth={3.4} /> : null}
                        </span>
                        {label}
                      </button>
                    </li>
                  ))}
                </ol>
                <p className="hp-flow">
                  <b>{open.flow}</b> {open.line}
                </p>
              </div>
            </div>
            <p className="hp-sr" aria-live="polite">
              {`Case ${openIndex + 1} open: ${open.label}.`}
            </p>
            <Badge size={104} on style={{ left: 674, top: 56, pointerEvents: "none" }}>
              <Cameo key={open.id} colour={open.sticker} state={stickerStateAt(open, t)} park={running ? null : ENTRANCE_MS} size={84} zoom={zoom ?? 1} gazeX={-0.6} gazeY={0.4} testId="helps-agent" style={{ position: "relative" }} />
            </Badge>
            <div role="group" aria-label="Other situations">
              {thumbs.map((situation, slot) => {
                const quad = thumbQuad(slot);
                return (
                  <button
                    key={situation.id}
                    ref={(node) => {
                      thumbRefs.current[slot] = node;
                    }}
                    type="button"
                    className="hp-thumb"
                    data-testid={`helps-thumb-${situation.id}`}
                    style={{ ...quadBox(quad), clipPath: quadClip(quad) }}
                    tabIndex={slot === 0 ? 0 : -1}
                    onClick={() => openCase(situation.id, slot)}
                    onKeyDown={(event) => moveFocus(event, slot)}
                  >
                    <span className="hp-caption">
                      <i>0{SITUATIONS.indexOf(situation) + 1}</i>
                      <span>{situation.label}</span>
                    </span>
                    <span className="hp-thumb-preview">{situation.preview}</span>
                    <span className="hp-arrow" aria-hidden="true">
                      ↗
                    </span>
                  </button>
                );
              })}
            </div>
            <svg className="hp-borders" viewBox={`0 0 ${HELPS_PAGE.width} ${HELPS_PAGE.height}`} aria-hidden="true">
              <polygon points={quadPoints(OPEN_QUAD)} fill="none" stroke="#000" strokeWidth={3} strokeLinejoin="miter" vectorEffect="non-scaling-stroke" />
              {THUMB_SLOTS.map((slot) => (
                <polygon key={slot} points={quadPoints(thumbQuad(slot))} fill="none" stroke="#000" strokeWidth={3} strokeLinejoin="miter" vectorEffect="non-scaling-stroke" />
              ))}
            </svg>
          </div>
        </div>
      </div>
    </section>
  );
}
