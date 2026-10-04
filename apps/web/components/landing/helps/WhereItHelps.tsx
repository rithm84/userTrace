/** The case board: three customer situations as comic panels, one open, two waiting as thumbnails. */
"use client";

import { useEffect, useRef, useState, type JSX, type KeyboardEvent } from "react";

import { useReducedMotion } from "@/components/agent/useReducedMotion";

import { Badge } from "../strip/art";
import { Cameo, ENTRANCE_MS } from "../strip/pieces";
import { HELPS_PAGE, OPEN_QUAD, quadBox, quadClip, quadPoints, thumbQuad } from "./helpsGeometry";
import { HelpsExample, SITUATIONS, type SituationId } from "./helpsExamples";
import "../strip/strip.css";
import "./helps.css";

const THUMB_SLOTS = [0, 1] as const;

export function WhereItHelps(): JSX.Element {
  const [openId, setOpenId] = useState<SituationId>(SITUATIONS[0].id);
  const thumbRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusSlot = useRef<number | null>(null);
  const reducedMotion = useReducedMotion();
  const open = SITUATIONS.find((situation) => situation.id === openId);
  if (open === undefined) throw new Error(`Unknown situation ${openId}.`);
  const openIndex = SITUATIONS.indexOf(open);
  const thumbs = SITUATIONS.filter((situation) => situation.id !== openId);

  // a thumbnail that was just opened is gone from the row, so focus stays in its slot
  useEffect(() => {
    if (focusSlot.current === null) return;
    thumbRefs.current[focusSlot.current]?.focus();
    focusSlot.current = null;
  }, [openId]);

  function openCase(id: SituationId, slot: number): void {
    focusSlot.current = slot;
    setOpenId(id);
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
      <div className="helps-wrap">
        <p className="lp-kicker helps-kicker">Where uTrace helps</p>
        <h2 className="lp-h2 helps-title" id="helps-title">
          From product failures
          <br />
          to missing capabilities.
        </h2>
        <p className="lp-lede helps-lede">A Product Signal starts as something a customer said or did. uTrace runs the right flow for it and comes back with evidence, not a ticket.</p>

        <div className="hp-page" data-testid="helps-page" data-open={openId} style={{ width: HELPS_PAGE.width, height: HELPS_PAGE.height }}>
          <div className="hp-open" role="region" aria-live="polite" aria-label={`Case ${openIndex + 1}: ${open.label}`} style={{ ...quadBox(OPEN_QUAD), clipPath: quadClip(OPEN_QUAD) }}>
            <div key={open.id} className="hp-open-body">
              <span className="hp-caption">
                <i>0{openIndex + 1}</i>
                <span>{open.label}</span>
              </span>
              <div className="hp-crop" data-testid="helps-example" data-situation={open.id}>
                <HelpsExample situation={open} />
              </div>
              <p className="hp-flow">
                <b>{open.flow}</b> {open.line}
              </p>
            </div>
          </div>
          <Badge size={104} on style={{ left: 664, top: 56, pointerEvents: "none" }}>
            <Cameo key={open.id} colour={open.sticker.colour} state={open.sticker.state} park={reducedMotion ? ENTRANCE_MS : null} size={84} gazeX={-0.6} gazeY={0.4} testId="helps-agent" style={{ position: "relative" }} />
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
    </section>
  );
}
