/**
 * Three customer situations at Shopalytics, each played as the concrete product moment it
 * happens in: the problem as the customer meets it, uTrace on it, the build, and the fixed
 * version the customer tries. None of them is the saved-view case the demo already tells, so
 * the board adds range instead of repeating the plot. Every frame is a pure function of `t`,
 * the case's clock (`caseClock.ts`).
 */
"use client";

import type { CharacterState } from "@utrace/agent-character";
import "@fontsource-variable/noto-sans";
import { Bell, Check } from "lucide-react";
import { useId, useRef, type JSX, type ReactNode } from "react";

import { CursorArrow } from "../art";
import type { AgentColour } from "../characters/AgentSprite";
import { pointerAt, sceneAt, type Point, type Rect, type SceneStep, type Waypoint } from "../story/agentScene";
import { AgentCursor, AgentScene } from "../story/PreviewTab";
import { SHOPALYTICS } from "../story/shopalyticsFixture";
import { useTargets } from "../story/useTargets";
import { CASE_TIMES, phaseAt, ramp } from "./caseClock";

export type SituationId = "failure" | "capability" | "workaround";

type Situation = Readonly<{
  id: SituationId;
  label: string;
  /** One line under the label on the thumbnail. */
  preview: string;
  flow: string;
  /** What uTrace does with it. */
  line: string;
  /** The specialist stuck on the open panel's corner. */
  sticker: AgentColour;
  /** The specialist's state while the customer meets the problem, then while uTrace is on it. */
  states: Readonly<{ problem: CharacterState; watch: CharacterState }>;
  /** The case's rail: how uTrace gets to the problem, the build, the customer's sign-off. */
  steps: readonly [string, string, string];
}>;

export const SITUATIONS: readonly Situation[] = [
  {
    id: "failure",
    label: "Something that used to work fails",
    preview: "The October channel report will not export any more.",
    flow: "Bugfix Flow.",
    line: "The complaint: the October channel report won’t export anymore. uTrace has the user run the export again while it watches and traces the failure to a timeout once the report passes a million rows. With the export fixed, the user watches the October report go through.",
    sticker: "sky",
    states: { problem: "concern", watch: "investigating" },
    steps: ["Reproduce", "Fix", "Validate"],
  },
  {
    id: "capability",
    label: "A user asks for a capability",
    preview: "Conversion fell over the weekend and nobody noticed until Monday.",
    flow: "Feature Request Flow.",
    line: "The ask: a Slack ping when conversion drops under 2%. uTrace asks what the alert is for, learns a weekend drop went unnoticed until Monday, notes the critical impact on revenue, and builds the alert.",
    sticker: "lilac",
    states: { problem: "listening", watch: "investigating" },
    steps: ["Interview", "Build", "Validate"],
  },
  {
    id: "workaround",
    label: "A workaround has become the workflow",
    preview: "Two CSV exports and a spreadsheet formula, every Monday.",
    flow: "Friction goes to the flow it belongs to.",
    line: "The habit: two CSV exports and a formula for a simple percent change, every Monday. uTrace asks what is done by hand, how often, and what the product should have kept, then builds the week-over-week comparison into the dashboard as a native feature.",
    sticker: "aqua",
    states: { problem: "concern", watch: "listening" },
    steps: ["Interview", "Build", "Validate"],
  },
];

/** The sticker's state `t` ms into the case: on the problem, building, watching the customer try the fix, then done. */
export function stickerStateAt(situation: Situation, t: number): CharacterState {
  switch (phaseAt(t)) {
    case "problem":
      return situation.states.problem;
    case "watch":
      return situation.states.watch;
    case "build":
      return "building";
    case "fixed":
      return "listening";
    case "done":
      return "completion";
  }
}

/** The crop's design size; targets, the agent's marks and both cursors are measured in it. */
const CROP = Object.freeze({ width: 560, height: 336 });
const { fixed: FIXED } = CASE_TIMES;
/** How long a pressed control stays pressed under the customer's pointer. */
const PRESS_MS = 160;

function pressed(t: number, at: number): boolean {
  return t >= at && t < at + PRESS_MS;
}

/** A point the customer's pointer can rest on, as a target the size of a point. */
function spot(point: Point): Rect {
  return { x: point.x, y: point.y, width: 0, height: 0 };
}

/**
 * One product window inside the crop, with what moves over it: the agent's coral cursor and the
 * marks it draws (the extension's own, `story/agentScene.ts`) and the customer's own pointer.
 * `layout` changes whenever a registered target appears, moves or goes.
 */
function Stage(
  props: Readonly<{ app: string; t: number; layout: string; agent: readonly SceneStep[]; pointer: readonly Waypoint[]; rest: Point; children: ReactNode }>,
): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  const measured = useTargets(root, CROP.width, props.layout);
  const targets = { ...measured, rest: spot(props.rest) };
  const scene = sceneAt(props.agent, targets, props.t);
  const pointer = pointerAt(props.pointer, targets, props.rest, props.t);
  return (
    <div ref={root} className="hp-stage">
      <div className="hp-scene">
        <div className="hp-scene-head">{props.app}</div>
        {props.children}
      </div>
      <AgentScene frame={scene} width={CROP.width} height={CROP.height} testPrefix="helps" />
      <CursorArrow size={16} style={{ position: "absolute", left: pointer.x - 2, top: pointer.y - 1, zIndex: 5 }} />
      {scene.cursor !== null ? <AgentCursor at={scene.cursor} testId="helps-agent-cursor" /> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ the export that fails */

const REPORTS = ["Conversion by channel · Oct 2026", "Top products · Oct 2026", "Sessions by device · Oct 2026"] as const;
const REPORT_ROWS = 1_203_441;
/** Where the old export gives up: past a million rows. */
const STALL = 0.87;
const EXPORT = Object.freeze({ firstRun: [200, 1800], firstFail: 1950, retry: 2950, secondRun: [3150, 4550], secondFail: 4700, fixedRun: [FIXED + 300, FIXED + 2100], ready: FIXED + 2200, download: FIXED + 2800 });
const EXPORT_REST: Point = { x: 520, y: 252 };
const EXPORT_POINTER: Readonly<{ problem: readonly Waypoint[]; fixed: readonly Waypoint[] }> = {
  problem: [
    { at: 2400, target: "retry" },
    { at: 3300, target: "rest" },
  ],
  fixed: [{ at: FIXED + 2300, target: "download" }],
};
const EXPORT_AGENT: Readonly<{ problem: readonly SceneStep[]; fixed: readonly SceneStep[] }> = {
  problem: [{ kind: "circle", target: "rows", at: 4200 }],
  fixed: [{ kind: "circle", target: "rows", at: FIXED + 2300 }],
};

function thousands(rows: number): number {
  return Math.round(rows / 1000) * 1000;
}

/**
 * The export dialog over the reports list. The old version stalls past a million rows and times
 * out, once as the customer meets it and once more when they try again while uTrace watches;
 * the fixed version runs the whole October report through.
 */
function ExportFailure(props: Readonly<{ t: number }>): JSX.Element {
  const { t } = props;
  const fixed = t >= FIXED;
  let progress: number;
  let status: "running" | "failed" | "ready";
  if (fixed) {
    progress = ramp(t, EXPORT.fixedRun[0], EXPORT.fixedRun[1]);
    status = t >= EXPORT.ready ? "ready" : "running";
  } else {
    const second = t >= EXPORT.retry;
    progress = STALL * (second ? ramp(t, EXPORT.secondRun[0], EXPORT.secondRun[1]) : ramp(t, EXPORT.firstRun[0], EXPORT.firstRun[1]));
    status = (!second && t >= EXPORT.firstFail) || t >= EXPORT.secondFail ? "failed" : "running";
  }
  const rows = progress === 1 ? REPORT_ROWS : thousands(REPORT_ROWS * progress);
  return (
    <Stage app={`${SHOPALYTICS.client} / Reports`} t={t} layout={`${fixed}|${status}`} agent={fixed ? EXPORT_AGENT.fixed : EXPORT_AGENT.problem} pointer={fixed ? EXPORT_POINTER.fixed : EXPORT_POINTER.problem} rest={EXPORT_REST}>
      <ul className="hp-reports" aria-hidden="true">
        {REPORTS.map((report) => (
          <li key={report}>
            <span>{report}</span>
            <span>Export</span>
          </li>
        ))}
      </ul>
      <div className="hp-scrim" aria-hidden="true" />
      <div className="hp-dialog" role="dialog" aria-label="Export report" data-testid="helps-export" data-status={status} data-version={fixed ? "fixed" : "problem"}>
        <div className="hp-dialog-head">
          <h4>Export report</h4>
          <span aria-hidden="true">×</span>
        </div>
        <dl className="hp-fields">
          <div>
            <dt>Report</dt>
            <dd>{REPORTS[0]}</dd>
          </div>
          <div>
            <dt>Format</dt>
            <dd>
              CSV<span aria-hidden="true">⌄</span>
            </dd>
          </div>
        </dl>
        <div className="hp-progress" aria-hidden="true">
          <i style={{ width: `${(progress * 100).toFixed(2)}%` }} />
        </div>
        <p className="hp-progress-note">
          <span data-target="rows">
            {rows.toLocaleString("en-US")} of {REPORT_ROWS.toLocaleString("en-US")} rows
          </span>
        </p>
        {status === "failed" ? (
          <div className="hp-status is-error">
            <span aria-hidden="true">!</span>Export failed after 60 s · request_timeout
          </div>
        ) : status === "ready" ? (
          <div className="hp-status is-ready">
            <span aria-hidden="true">
              <Check size={13} strokeWidth={3.2} />
            </span>
            Export ready · conversion-by-channel-oct-2026.csv
          </div>
        ) : (
          <div className="hp-status is-empty" aria-hidden="true" />
        )}
        <div className="hp-dialog-actions">
          <span className="hp-btn-ghost">Cancel</span>
          {fixed ? (
            <span className={"hp-btn" + (status === "ready" ? "" : " is-disabled") + (pressed(t, EXPORT.download) ? " is-pressed" : "")} data-target="download">
              Download CSV
            </span>
          ) : (
            <span className={"hp-btn" + (pressed(t, EXPORT.retry) ? " is-pressed" : "")} data-target="retry">
              Try again
            </span>
          )}
        </div>
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ the weekend drop and the alert */

/** Fourteen days of conversion rate, in percent, with the weekend dip nobody was watching. */
const CONVERSION = [2.31, 2.28, 2.35, 2.3, 2.26, 2.33, 2.29, 2.34, 2.3, 2.27, 2.25, 1.62, 1.41, 1.44] as const;
const SPARK = Object.freeze({ width: 500, height: 96, min: 1.2, max: 2.5 });
const THRESHOLD = 2;
const DROP = Object.freeze({ draw: [200, 1900], delta: 1900, toggle: FIXED + 750, slack: FIXED + 1500 });
const DROP_REST: Point = { x: 470, y: 312 };
const DROP_POINTER: Readonly<{ problem: readonly Waypoint[]; fixed: readonly Waypoint[] }> = {
  problem: [{ at: 2000, target: "sunday" }],
  fixed: [{ at: FIXED + 150, target: "toggle" }],
};
const DROP_AGENT: Readonly<{ problem: readonly SceneStep[]; fixed: readonly SceneStep[] }> = {
  problem: [{ kind: "circle", target: "dip", at: 3300 }],
  fixed: [{ kind: "circle", target: "threshold", at: FIXED + 2200 }],
};

function sparkY(value: number): number {
  return SPARK.height - ((value - SPARK.min) / (SPARK.max - SPARK.min)) * SPARK.height;
}

function sparkPath(values: readonly number[]): string {
  return values.map((value, index) => `${index === 0 ? "M" : "L"}${((index / (values.length - 1)) * SPARK.width).toFixed(1)} ${sparkY(value).toFixed(1)}`).join(" ");
}

/**
 * The drop as the dashboard shows it on Monday morning; the fixed version adds the alert the
 * customer asked for, under 2% in #growth, and its first ping, since conversion is under it now.
 * The agent marks only Shopalytics, where the extension draws, never the Slack message.
 */
function WeekendDrop(props: Readonly<{ t: number }>): JSX.Element {
  const { t } = props;
  const clip = useId();
  const fixed = t >= FIXED;
  const drawn = fixed ? 1 : ramp(t, DROP.draw[0], DROP.draw[1]);
  const alertOn = fixed && t >= DROP.toggle;
  const pinged = fixed && t >= DROP.slack;
  const { width, height } = SPARK;
  const line = sparkPath(CONVERSION);
  const weekendStart = (10.5 / 13) * width;
  const threshold = sparkY(THRESHOLD);
  return (
    <Stage app={`${SHOPALYTICS.client} / Conversion`} t={t} layout={`${fixed}|${pinged}`} agent={fixed ? DROP_AGENT.fixed : DROP_AGENT.problem} pointer={fixed ? DROP_POINTER.fixed : DROP_POINTER.problem} rest={DROP_REST}>
      <div className="hp-kpi" data-testid="helps-drop" data-version={fixed ? "fixed" : "problem"}>
        <div className="hp-kpi-head">
          <b>Conversion rate</b>
          <span>Last 14 days · all channels</span>
        </div>
        <div className="hp-kpi-value">
          1.44%
          <em style={{ visibility: fixed || t >= DROP.delta ? "visible" : "hidden" }}>−0.9 pp since Friday</em>
        </div>
        <div className="hp-spark-wrap">
          <svg className="hp-spark" viewBox={`0 0 ${width} ${height + 6}`} preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <clipPath id={clip}>
                <rect x={0} y={-4} width={width * drawn} height={height + 10} />
              </clipPath>
            </defs>
            {/* the weekend's shading draws on with the line, so it never shows ahead of it */}
            <g clipPath={`url(#${clip})`}>
              <rect x={weekendStart} y={0} width={width - weekendStart} height={height + 6} className="hp-spark-weekend" data-target="dip" />
              <path d={`${line} L${width} ${height + 6} L0 ${height + 6} Z`} className="hp-spark-area" />
              <path d={line} className="hp-spark-line" />
              <circle cx={(12 / 13) * width} cy={sparkY(1.41)} r={5} className="hp-spark-dot" />
            </g>
            {fixed ? <line x1={0} x2={width} y1={threshold} y2={threshold} className="hp-spark-threshold" /> : null}
          </svg>
          {fixed ? (
            <span className="hp-threshold-tag" data-target="threshold" style={{ top: `${(threshold / (height + 6)) * 100}%` }}>
              2%
            </span>
          ) : null}
        </div>
        <div className="hp-axis">
          <span>Mon</span>
          <span>Thu</span>
          <span>Sun</span>
          <span>Wed</span>
          <span className="hp-axis-weekend">Sat</span>
          <span>Mon</span>
        </div>
        {fixed ? (
          <div className="hp-alert">
            <Bell size={14} strokeWidth={2.4} aria-hidden="true" />
            <b>Slack alert</b>
            <span>when conversion is under</span>
            <span className="hp-field">2%</span>
            <span>in</span>
            <span className="hp-field">#growth</span>
            <span className={"hp-toggle" + (alertOn ? " is-on" : "") + (pressed(t, DROP.toggle) ? " is-pressed" : "")} data-target="toggle" role="switch" aria-checked={alertOn} aria-label="Slack alert">
              <i />
            </span>
          </div>
        ) : (
          <dl className="hp-days">
            {[
              ["Fri", "2.25%"],
              ["Sat", "1.62%"],
              ["Sun", "1.41%"],
              ["Mon", "1.44%"],
            ].map(([day, rate]) => (
              <div key={day} className={day === "Sat" || day === "Sun" ? "is-weekend" : undefined} data-target={day === "Sun" ? "sunday" : undefined}>
                <dt>{day}</dt>
                <dd>{rate}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
      {pinged ? <SlackPing /> : null}
    </Stage>
  );
}

/** The alert's first ping in #growth, as Slack's dark theme shows an app's message: the channel header, then the message with its button. */
function SlackPing(): JSX.Element {
  return (
    <div className="hp-slack" data-testid="helps-slack">
      <div className="sl">
        <div className="sl-head">
          <span aria-hidden="true">#</span>growth
        </div>
        <div className="sl-message">
          {/* eslint-disable-next-line @next/next/no-img-element -- a tiny fixture avatar inside the illustration */}
          <img className="sl-avatar" src={SHOPALYTICS.clientLogoSrc} alt="" width={36} height={36} />
          <div className="sl-body">
            <p className="sl-meta">
              <b>{SHOPALYTICS.client}</b>
              <span className="sl-app">APP</span>
              <span className="sl-time">9:41 AM</span>
            </p>
            <p className="sl-text">
              Conversion is <b>1.44%</b>, under your 2% alert.
            </p>
            <span className="sl-button">View dashboard</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ the Monday spreadsheet */

const WEEKS = [
  { metric: "Sessions", before: "45,880", after: "48,210", change: "+5.1%" },
  { metric: "Conversion rate", before: "2.04%", after: "2.31%", change: "+13.2%" },
  { metric: "Add-to-cart rate", before: "6.1%", after: "6.8%", change: "+11.5%" },
] as const;
const FORMULA = "=C3/B3-1";
const SHEET = Object.freeze({ typing: [300, 1300], entered: 1450, fill: 2100, compare: FIXED + 800 });
const SHEET_REST: Point = { x: 500, y: 300 };
const SHEET_POINTER: Readonly<{ problem: readonly Waypoint[]; fixed: readonly Waypoint[] }> = {
  problem: [
    { at: 1650, target: "fill" },
    { at: 3200, target: "exports" },
  ],
  fixed: [{ at: FIXED + 250, target: "compare" }],
};
const SHEET_AGENT: Readonly<{ problem: readonly SceneStep[]; fixed: readonly SceneStep[] }> = {
  // the extension draws only in the customer's preview, never over their spreadsheet
  problem: [],
  fixed: [{ kind: "circle", target: "change", at: FIXED + 1800 }],
};

/**
 * The comparison the product does not offer, rebuilt by hand in a spreadsheet from two exports;
 * the fixed version is the same comparison inside Shopalytics, one switch away.
 */
function SpreadsheetWorkaround(props: Readonly<{ t: number }>): JSX.Element {
  const { t } = props;
  if (t >= FIXED) return <NativeComparison t={t} />;
  const typed = FORMULA.slice(0, Math.floor(ramp(t, SHEET.typing[0], SHEET.typing[1]) * FORMULA.length));
  const entered = t >= SHEET.entered;
  const filled = t >= SHEET.fill;
  const cells: readonly (readonly string[])[] = [
    ["Metric", "Week 35", "Week 36", "Change"],
    ...WEEKS.map((week, index) => [week.metric, week.before, week.after, index === 0 ? week.change : index === 1 ? (entered ? week.change : typed) : filled ? week.change : ""]),
  ];
  return (
    <Stage app="Sheets / compare-weeks" t={t} layout="sheet" agent={SHEET_AGENT.problem} pointer={SHEET_POINTER.problem} rest={SHEET_REST}>
      <div className="hp-sheet-app" data-testid="helps-sheet" data-version="problem">
        <div className="hp-formula">
          <span className="hp-cell-ref">{entered && filled ? "D4" : "D3"}</span>
          <span className="hp-fx">fx</span>
          <span className="hp-formula-text">{entered ? (filled ? "=C4/B4-1" : FORMULA) : typed}</span>
        </div>
        <table className="hp-grid">
          <thead>
            <tr>
              <th scope="col" />
              {["A", "B", "C", "D"].map((column) => (
                <th scope="col" key={column}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cells.map((row, rowIndex) => (
              <tr key={row[0]}>
                <th scope="row">{rowIndex + 1}</th>
                {row.map((cell, columnIndex) => {
                  const selected = columnIndex === 3 && rowIndex === (filled ? 3 : 2);
                  const className = rowIndex === 0 ? "is-header" : selected ? "is-selected" : columnIndex === 3 && cell !== "" ? "hp-typed" : undefined;
                  return (
                    <td key={columnIndex} className={className} data-target={columnIndex === 3 && rowIndex === 3 ? "fill" : undefined}>
                      {cell}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hp-sheet-tabs">
          <span className="hp-sheet-exports" data-target="exports">
            <span>week35.csv</span>
            <span>week36.csv</span>
          </span>
          <span className="is-active">compare</span>
        </div>
      </div>
    </Stage>
  );
}

function NativeComparison(props: Readonly<{ t: number }>): JSX.Element {
  const { t } = props;
  const on = t >= SHEET.compare;
  return (
    <Stage app={`${SHOPALYTICS.client} / Conversion`} t={t} layout={`compare|${on}`} agent={SHEET_AGENT.fixed} pointer={SHEET_POINTER.fixed} rest={SHEET_REST}>
      <div className="hp-compare" data-testid="helps-sheet" data-version="fixed">
        <div className="hp-compare-bar">
          <span className="hp-field">
            Week 36 · Aug 31 – Sep 6<span aria-hidden="true">⌄</span>
          </span>
          <span className="hp-switch">
            <span className={"hp-toggle" + (on ? " is-on" : "") + (pressed(t, SHEET.compare) ? " is-pressed" : "")} data-target="compare" role="switch" aria-checked={on} aria-label="Compare to previous week">
              <i />
            </span>
            Compare to previous week
          </span>
        </div>
        <table className="hp-compare-table">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {on ? <th scope="col">Week 35</th> : null}
              <th scope="col">Week 36</th>
              {on ? <th scope="col">Change</th> : null}
            </tr>
          </thead>
          <tbody>
            {WEEKS.map((week) => (
              <tr key={week.metric}>
                <th scope="row">{week.metric}</th>
                {on ? <td>{week.before}</td> : null}
                <td>{week.after}</td>
                {on ? (
                  <td className="hp-up">
                    {/* the target is the value, not the cell, so the agent's loop fits it */}
                    <span data-target={week.metric === "Conversion rate" ? "change" : undefined}>
                      <i aria-hidden="true">▲</i>
                      {week.change}
                    </span>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Stage>
  );
}

export function HelpsExample(props: Readonly<{ situation: Situation; t: number }>): JSX.Element {
  switch (props.situation.id) {
    case "failure":
      return <ExportFailure t={props.t} />;
    case "capability":
      return <WeekendDrop t={props.t} />;
    case "workaround":
      return <SpreadsheetWorkaround t={props.t} />;
  }
}
