"use client";

import { LogOut, Mic, Pause, PhoneOff } from "lucide-react";
import { useRef, type JSX } from "react";

import { CursorArrow } from "../art";
import { Cameo, ENTRANCE_MS } from "../pieces";
import { ShopalyticsDashboard, type DashboardProps } from "../surfaces/ShopalyticsDashboard";
import { centreOf, pointerAt, sceneAt, type Point, type SceneFrame, type SceneStep, type Waypoint } from "./agentScene";
import { PRODUCT_AGENT, type Beat, type BeatId } from "./beats";
import { SHOPALYTICS, type FilterChip } from "./shopalyticsFixture";
import { useTargets } from "./useTargets";

type Criterion = FilterChip["criterion"];

/** The dashboard is drawn at its own scale and enlarged to fill the tab. */
const SCALE = 1.25;
const ALL: readonly Criterion[] = SHOPALYTICS.filters.map((filter) => filter.criterion);
const LOST: Criterion = SHOPALYTICS.validation.partial.missingCriterion;
const NAME = SHOPALYTICS.savedViewName;
/** Maya sets one filter after another; each takes its value as her click lands. */
const FILTER_STEP_MS = 380;
const CLICK_MS = 400;
const TYPE_MS = 52;

/** The beats that happen in the preview tab. */
type PreviewBeat = Extract<BeatId, "filters" | "reset" | "save" | "partial" | "validated">;

/** Where Maya's pointer goes in each beat; once she has clicked, it rests on the chart, clear of what the agent marks. */
const MAYA: Readonly<Record<PreviewBeat, readonly Waypoint[]>> = {
  filters: [...ALL.map((criterion, index) => ({ at: 150 + index * FILTER_STEP_MS, target: `filter-${criterion}` })), { at: 150 + ALL.length * FILTER_STEP_MS + 200, target: "chart" }],
  reset: [],
  save: [{ at: 150, target: "view-name" }, { at: 1800, target: "save-view" }, { at: 2400, target: "chart" }],
  partial: [{ at: 250, target: "saved-view" }, { at: 950, target: "chart" }],
  validated: [{ at: 200, target: "saved-view" }, { at: 850, target: "chart" }],
};

/** What the agent shows in each beat, as the extension would draw it: labels and circles on registered targets. */
const AGENT: Readonly<Record<PreviewBeat, readonly SceneStep[]>> = {
  filters: [{ kind: "label", target: "filter-bar", text: "These seven, every Monday?", at: 2950 }, { kind: "circle", target: "filter-bar", at: 0 }],
  reset: [{ kind: "label", target: "filter-bar", text: "All gone after a reload", at: 800 }, { kind: "trace", target: "filter-bar", at: 0 }],
  save: [{ kind: "circle", target: "saved-view", at: 2700 }],
  partial: [{ kind: "circle", target: `filter-${LOST}`, at: 1000 }, { kind: "label", target: `filter-${LOST}`, text: "Sizing didn't come back", at: 0 }],
  validated: [{ kind: "label", target: "filter-bar", text: "All seven came back", at: 850 }, { kind: "circle", target: "filter-bar", at: 0 }],
};

function isPreviewBeat(id: BeatId): id is PreviewBeat {
  return id in MAYA;
}

/** The beat's id, which must be one of the preview's. */
function previewBeat(beat: Beat): PreviewBeat {
  if (!isPreviewBeat(beat.id)) throw new Error(`The ${beat.id} beat does not happen in the preview tab.`);
  return beat.id;
}

/** The product's state `t` ms into a preview beat. */
function productAt(id: PreviewBeat, t: number, candidate: boolean): DashboardProps {
  const base: DashboardProps = { applied: ALL, changed: null, flash: false, candidate, viewName: "", savedViews: candidate ? [NAME] : [], activeView: null, toast: null };
  switch (id) {
    case "filters": {
      const landed = ALL.filter((_, index) => t >= 150 + index * FILTER_STEP_MS + CLICK_MS);
      const last = landed.length - 1;
      const fresh = last >= 0 && t < 150 + last * FILTER_STEP_MS + CLICK_MS + 160;
      return { ...base, applied: landed, changed: fresh ? landed[last] : null };
    }
    case "reset":
      return { ...base, applied: t < 400 ? ALL : [], flash: t >= 400 && t < 600 };
    case "save": {
      const saved = t >= 2150;
      const typed = Math.max(0, Math.min(NAME.length, Math.floor((t - 550) / TYPE_MS)));
      return { ...base, viewName: saved ? "" : NAME.slice(0, typed), savedViews: saved ? [NAME] : [], toast: t >= 2200 && t < 3400 ? "View saved" : null };
    }
    case "partial": {
      const reopened = t >= 700;
      return { ...base, applied: reopened ? ALL.filter((criterion) => criterion !== LOST) : [], flash: t < 220, activeView: reopened ? NAME : null };
    }
    case "validated": {
      const reopened = t >= 650;
      return { ...base, applied: reopened ? ALL : [], flash: t < 200, activeView: reopened ? NAME : null };
    }
  }
}

/**
 * The Shopalytics preview tab: the product itself, the extension's floating agent with its four
 * controls, Maya's own pointer, and the agent's cursor gliding to registered targets to label
 * them and circle them the way the extension draws its scenes.
 */
export function PreviewTab(props: Readonly<{ beat: Beat; progress: number; active: boolean; width: number; height: number }>): JSX.Element {
  const { beat } = props;
  const id = previewBeat(beat);
  const box = { width: props.width / SCALE, height: props.height / SCALE };
  const root = useRef<HTMLDivElement>(null);
  const t = props.progress * beat.duration;
  const product = productAt(id, t, beat.chapter !== "interview");
  const targets = useTargets(root, box.width, `${id}|${product.applied.join()}|${product.savedViews.length}`);
  const scene = sceneAt(AGENT[id], targets, t);
  const maya = pointerAt(MAYA[id], targets, { x: box.width * 0.62, y: box.height * 0.86 }, t);
  const pills = id === "filters" && props.progress < 0.2;
  return (
    <div className="st-preview" data-testid="story-preview" data-filters={product.applied.length}>
      <div ref={root} className="st-dashboard" style={{ width: box.width, height: box.height, transform: `scale(${SCALE})` }}>
        <ShopalyticsDashboard {...product} />
        <AgentScene frame={scene} width={box.width} height={box.height} />
        <CursorArrow size={16} style={{ position: "absolute", left: maya.x - 2, top: maya.y - 1, zIndex: 5 }} />
        {scene.cursor !== null ? <AgentCursor at={scene.cursor} /> : null}
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
          <Cameo colour={PRODUCT_AGENT.colour} state={beat.agent.state} park={props.active ? null : ENTRANCE_MS} size={58} gazeX={-0.6} gazeY={-0.5} testId="story-extension-agent" style={{ position: "relative" }} />
        </span>
      </div>
    </div>
  );
}

/** The 13px bold label's width, near enough to keep it inside the tab. */
function labelWidth(text: string): number {
  return text.length * 7.1 + 24;
}

/** The agent's marks and labels: coral strokes drawn on, and the extension's ink-edged label under its target. */
function AgentScene(props: Readonly<{ frame: SceneFrame; width: number; height: number }>): JSX.Element {
  return (
    <>
      <svg className="st-scene" width={props.width} height={props.height} viewBox={`0 0 ${props.width} ${props.height}`} aria-hidden="true">
        {props.frame.marks.map((mark) => (mark.drawn > 0 ? <path key={mark.key} d={mark.d} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - mark.drawn} opacity={mark.opacity} data-testid="story-agent-mark" /> : null))}
      </svg>
      {props.frame.labels.map((label) => {
        const half = labelWidth(label.text) / 2;
        const centre = centreOf(label.target);
        return (
          <b key={label.key} className="st-scene-label" data-testid="story-agent-label" style={{ left: Math.min(Math.max(centre.x, half + 8), props.width - half - 8), top: label.target.y + label.target.height + 10 }}>
            {label.text}
          </b>
        );
      })}
    </>
  );
}

/** The extension's agent cursor: coral with an ink edge, its tip on the point. */
function AgentCursor(props: Readonly<{ at: Point }>): JSX.Element {
  return (
    <svg className="st-agent-cursor" data-testid="story-agent-cursor" width={22} height={22} viewBox="0 0 26 26" aria-hidden="true" style={{ left: props.at.x - 3, top: props.at.y - 2 }}>
      <path d="M3 2 L3 20 L8 15.5 L11.5 23 L15 21.2 L11.6 14 L18.5 13.4 Z" fill="#fcada3" stroke="#000000" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}
