"use client";

import { LogOut, Mic, Pause, PhoneOff } from "lucide-react";
import { useRef, type JSX } from "react";

import { CursorArrow } from "../strip/art";
import { Cameo, ENTRANCE_MS } from "../strip/pieces";
import { ShopalyticsDashboard } from "../strip/surfaces/ShopalyticsDashboard";
import { useAnchor } from "../strip/useAnchor";
import { SHOPALYTICS } from "./shopalyticsFixture";
import type { Beat } from "./beats";

const CHIPS = SHOPALYTICS.filters;
/** Where the filter the partial restore lost stood among the chips. */
const MISSING_AT = CHIPS.findIndex((chip) => chip.criterion === SHOPALYTICS.validation.partial.missingCriterion);
if (MISSING_AT === -1) throw new Error(`The partial restore's missing criterion ${SHOPALYTICS.validation.partial.missingCriterion} is not one of the fixture's filters.`);

/** Held frames: progress quantised to `steps`. */
function held(progress: number, steps: number): number {
  return Math.floor(progress * steps) / steps;
}

/**
 * The Shopalytics preview tab: the real dashboard, the extension's floating agent with its
 * four controls, the agent's cursor pointing and circling registered targets, and Maya's own
 * cursor. Everything the agent shows is anchored to a real element, never a guessed pixel.
 */
export function PreviewTab(props: Readonly<{ beat: Beat; progress: number; active: boolean; width: number; height: number }>): JSX.Element {
  const { beat, progress } = props;
  const scale = 1.3;
  const box = { width: props.width / scale, height: props.height / scale };
  const root = useRef<HTMLDivElement>(null);

  const chips = beat.id === "filters" ? Math.min(CHIPS.length, Math.floor(held(progress, 12) * 12) - 1) : beat.id === "reset" ? (progress < 0.2 ? CHIPS.length : 0) : beat.id === "save" ? CHIPS.length : beat.id === "partial" ? (progress < 0.25 ? 0 : CHIPS.length - 1) : CHIPS.length;
  const filters = beat.id === "partial" && progress >= 0.25 ? CHIPS.filter((chip) => chip.criterion !== SHOPALYTICS.validation.partial.missingCriterion) : CHIPS.slice(0, Math.max(0, chips));
  const missing = beat.id === "partial" && progress >= 0.25 ? { criterion: SHOPALYTICS.validation.partial.missingCriterion, at: MISSING_AT } : null;
  const candidate = beat.chapter !== "interview";
  const saved = beat.id === "save" ? progress >= 0.6 : beat.id === "partial" || beat.id === "validated";
  // she left after saving: the partial beat opens on the empty dashboard, and she reopens the view as it flashes
  const reopened = beat.id === "partial" ? progress >= 0.22 : saved && beat.id !== "save";
  const flash = (beat.id === "reset" && progress >= 0.2 && progress < 0.24) || (beat.id === "partial" && progress >= 0.22 && progress < 0.26);
  const landed = beat.id === "filters" && chips > 0 && held(progress, 12) * 12 - chips < 0.6;

  const bar = useAnchor(root, ".sh-filters", box.width, filters.length);
  const add = useAnchor(root, ".sh-add", box.width, filters.length);
  const save = useAnchor(root, ".sh-save", box.width, saved ? 1 : 0);
  const slot = useAnchor(root, ".sh-missing", box.width, missing === null ? 0 : 1);
  const view = useAnchor(root, ".sh-view", box.width, saved ? 1 : 0);

  const agentRing = beat.id === "filters" && progress >= 0.7 ? bar : beat.id === "partial" && slot !== null ? slot : beat.id === "validated" && progress >= 0.3 ? bar : null;
  const tag = beat.id === "partial" && progress >= 0.45 ? "partially validated" : beat.id === "validated" && progress >= 0.3 ? "validated" : null;
  const agentLabel = beat.id === "filters" ? "these seven?" : beat.id === "partial" ? (tag === null ? "sizing didn't come back" : "partially validated · sizing didn't come back") : beat.id === "validated" ? (tag === null ? "all seven back" : "validated · all seven back") : null;
  const agentPoint = beat.id === "reset" && progress >= 0.35 ? bar : null;
  const mayaCursor = beat.id === "filters" && chips < CHIPS.length ? add : beat.id === "save" ? (progress < 0.6 ? save : view) : beat.id === "partial" && !reopened ? view : null;
  // the agent's note hangs under what it marks, inside the dashboard: from the mark's left edge, or its right edge when the mark sits in the right half
  const labelSpot = agentRing === null ? null : agentRing.x > box.width / 2 ? { right: box.width - (agentRing.x + agentRing.width / 2) - 4, top: agentRing.y + agentRing.height / 2 + 10 } : { left: agentRing.x - agentRing.width / 2 + 4, top: agentRing.y + agentRing.height / 2 + 10 };
  const pills = beat.id === "filters" && progress < 0.3;

  return (
    <div className="st-preview" data-testid="story-preview" data-filters={filters.length} data-tag={tag ?? undefined}>
      <div ref={root} className="st-dashboard" style={{ width: box.width, height: box.height, transform: `scale(${scale})` }}>
        <ShopalyticsDashboard filters={filters} chart={1} flash={flash} popLast={landed} missing={missing} saveView={candidate} savedViews={saved ? [SHOPALYTICS.savedViewName] : candidate ? [] : undefined} activeView={reopened ? SHOPALYTICS.savedViewName : null} toast={beat.id === "save" && progress >= 0.6 && progress < 0.85 ? "View saved" : null} />
        {agentRing !== null ? <span className="st-agent-ring" data-testid="story-agent-ring" style={{ left: agentRing.x - agentRing.width / 2 - 8, top: agentRing.y - agentRing.height / 2 - 6, width: agentRing.width + 16, height: agentRing.height + 12 }} /> : null}
        {agentLabel !== null && (agentRing !== null || tag !== null) ? (
          <b className={"st-agent-label" + (tag === "validated" ? " is-good" : "")} data-testid={tag !== null ? "story-validation-tag" : "story-agent-label"} style={labelSpot ?? { left: 30, top: 118, visibility: "hidden" }}>
            {agentLabel}
          </b>
        ) : null}
        {agentPoint !== null ? (
          <span className="st-agent-point" data-testid="story-agent-point" style={{ left: agentPoint.x - 30, top: agentPoint.y + 8 }}>
            <CursorArrow size={18} style={{ display: "block" }} />
            <b>and it&rsquo;s gone</b>
          </span>
        ) : null}
        {mayaCursor !== null ? <CursorArrow size={18} style={{ position: "absolute", left: mayaCursor.x - 3, top: mayaCursor.y - 2, zIndex: 4 }} /> : null}
      </div>
      <div className={"st-extension" + (pills ? " is-open" : "")} data-testid="story-extension">
        <span className="st-ext-pill is-up">
          <LogOut size={12} />
          Return to session
        </span>
        <span className="st-ext-pill is-left">
          <Mic size={12} />
          Mute
        </span>
        <span className="st-ext-pill is-right">
          <Pause size={12} />
          Pause
        </span>
        <span className="st-ext-pill is-down">
          <PhoneOff size={12} />
          End interview
        </span>
        <span className="st-ext-agent">
          <Cameo colour={beat.agent.colour} state={beat.agent.state} park={props.active ? null : ENTRANCE_MS} size={58} gazeX={-0.6} gazeY={-0.5} testId="story-extension-agent" style={{ position: "relative" }} />
        </span>
      </div>
    </div>
  );
}
