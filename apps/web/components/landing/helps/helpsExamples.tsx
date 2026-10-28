/**
 * Three customer situations at Shopalytics, each shown as the concrete product moment it
 * happens in. None of them is the saved-view case the demo already tells, so
 * the board adds range instead of repeating the plot.
 */
import type { CharacterState } from "@utrace/agent-character";
import type { JSX, ReactNode } from "react";

import type { AgentColour } from "../characters/AgentSprite";
import { SHOPALYTICS } from "../story/shopalyticsFixture";

export type SituationId = "failure" | "capability" | "workaround";

export type Situation = Readonly<{
  id: SituationId;
  label: string;
  /** One line under the label on the thumbnail. */
  preview: string;
  flow: string;
  /** What uTrace does with it, in one sentence. */
  line: string;
  /** The specialist stuck on the open panel's corner. */
  sticker: Readonly<{ colour: AgentColour; state: CharacterState }>;
}>;

export const SITUATIONS: readonly Situation[] = [
  {
    id: "failure",
    label: "Something that used to work fails",
    preview: "The October channel report will not export any more.",
    flow: "Bugfix Flow.",
    line: "The failure: the October channel report times out at a million rows. uTrace asks what was expected and what happened, gets a reproduction trace of the export, and confirms with the user that this is the issue.",
    sticker: { colour: "sky", state: "investigating" },
  },
  {
    id: "capability",
    label: "A user asks for a capability",
    preview: "Conversion fell over the weekend and nobody noticed until Monday.",
    flow: "Feature Request Flow.",
    line: "The ask: a Slack ping when conversion drops under 2%. uTrace finds the job behind it and whether the ask would relieve it. If not, it proposes a better fit and proceeds once the user agrees.",
    sticker: { colour: "lilac", state: "listening" },
  },
  {
    id: "workaround",
    label: "A workaround has become the workflow",
    preview: "Two CSV exports and a spreadsheet formula, every Monday.",
    flow: "Friction goes to the flow it belongs to.",
    line: "The habit: two CSV exports and a formula, every Monday. uTrace asks what is done by hand, how often, and what the product should have kept, then routes the missing week-over-week comparison to the Feature Request Flow.",
    sticker: { colour: "aqua", state: "concern" },
  },
];

function Scene(props: Readonly<{ app: string; children: ReactNode }>): JSX.Element {
  return (
    <div className="hp-scene">
      <div className="hp-scene-head">
        <span>{props.app}</span>
        <span>Illustrative example</span>
      </div>
      {props.children}
    </div>
  );
}

const REPORTS = ["Conversion by channel · Oct 2026", "Top products · Oct 2026", "Sessions by device · Oct 2026"] as const;

/** The export dialog over the reports list, failing on the one report the team sends every month. */
function ExportFailure(): JSX.Element {
  return (
    <Scene app={`${SHOPALYTICS.client} / Reports`}>
      <ul className="hp-reports" aria-hidden="true">
        {REPORTS.map((report) => (
          <li key={report}>
            <span>{report}</span>
            <span>Export</span>
          </li>
        ))}
      </ul>
      <div className="hp-scrim" aria-hidden="true" />
      <div className="hp-dialog" role="dialog" aria-label="Export report">
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
          <i style={{ width: "87%" }} />
        </div>
        <p className="hp-progress-note">1,047,000 of 1,203,441 rows</p>
        <div className="hp-error">
          <span aria-hidden="true">!</span>Export failed after 60 s · request_timeout
        </div>
        <div className="hp-dialog-actions">
          <span className="hp-btn-ghost">Cancel</span>
          <span className="hp-btn">Try again</span>
        </div>
      </div>
    </Scene>
  );
}

/** Fourteen days of conversion rate, in percent, with the weekend dip nobody was watching. */
const CONVERSION = [2.31, 2.28, 2.35, 2.3, 2.26, 2.33, 2.29, 2.34, 2.3, 2.27, 2.25, 1.62, 1.41, 1.44] as const;

function sparkPath(values: readonly number[], width: number, height: number, min: number, max: number): string {
  return values.map((value, index) => `${index === 0 ? "M" : "L"}${((index / (values.length - 1)) * width).toFixed(1)} ${(height - ((value - min) / (max - min)) * height).toFixed(1)}`).join(" ");
}

/** The drop as the dashboard shows it on Monday morning. */
function WeekendDrop(): JSX.Element {
  const width = 500;
  const height = 96;
  const line = sparkPath(CONVERSION, width, height, 1.2, 2.5);
  const weekendStart = (10.5 / 13) * width;
  const sundayX = (12 / 13) * width;
  const sundayY = height - ((1.41 - 1.2) / 1.3) * height;
  return (
    <Scene app={`${SHOPALYTICS.client} / Conversion`}>
      <div className="hp-kpi">
        <div className="hp-kpi-head">
          <b>Conversion rate</b>
          <span>Last 14 days · all channels</span>
        </div>
        <div className="hp-kpi-value">
          1.44%
          <em>−0.9 pp since Friday</em>
        </div>
        <svg className="hp-spark" viewBox={`0 0 ${width} ${height + 6}`} preserveAspectRatio="none" aria-hidden="true">
          <rect x={weekendStart} y={0} width={width - weekendStart} height={height + 6} className="hp-spark-weekend" />
          <path d={`${line} L${width} ${height + 6} L0 ${height + 6} Z`} className="hp-spark-area" />
          <path d={line} className="hp-spark-line" />
          <circle cx={sundayX} cy={sundayY} r={5} className="hp-spark-dot" />
        </svg>
        <div className="hp-axis">
          <span>Mon</span>
          <span>Thu</span>
          <span>Sun</span>
          <span>Wed</span>
          <span className="hp-axis-weekend">Sat</span>
          <span>Mon</span>
        </div>
        <dl className="hp-days">
          {[["Fri", "2.25%"], ["Sat", "1.62%"], ["Sun", "1.41%"], ["Mon", "1.44%"]].map(([day, rate]) => (
            <div key={day} className={day === "Sat" || day === "Sun" ? "is-weekend" : undefined}>
              <dt>{day}</dt>
              <dd>{rate}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Scene>
  );
}

const SHEET = [
  ["Metric", "Week 35", "Week 36", "Change"],
  ["Sessions", "45,880", "48,210", "+5.1%"],
  ["Conversion rate", "2.04%", "2.31%", "+13.2%"],
  ["Add-to-cart rate", "6.1%", "6.8%", "+11.5%"],
] as const;

/** The comparison the product does not offer, rebuilt by hand in a spreadsheet from two exports. */
function SpreadsheetWorkaround(): JSX.Element {
  return (
    <Scene app="Sheets / compare-weeks">
      <div className="hp-sheet-app">
        <div className="hp-formula">
          <span className="hp-cell-ref">D3</span>
          <span className="hp-fx">fx</span>
          <span className="hp-formula-text">=C3/B3-1</span>
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
            {SHEET.map((row, rowIndex) => (
              <tr key={row[0]}>
                <th scope="row">{rowIndex + 1}</th>
                {row.map((cell, columnIndex) => (
                  <td key={columnIndex} className={rowIndex === 2 && columnIndex === 3 ? "is-selected" : rowIndex === 0 ? "is-header" : columnIndex === 3 ? "hp-typed" : undefined}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hp-sheet-tabs">
          <span>week35.csv</span>
          <span>week36.csv</span>
          <span className="is-active">compare</span>
          <span className="hp-chip">every Monday</span>
        </div>
      </div>
    </Scene>
  );
}

export function HelpsExample(props: Readonly<{ situation: Situation }>): JSX.Element {
  switch (props.situation.id) {
    case "failure":
      return <ExportFailure />;
    case "capability":
      return <WeekendDrop />;
    case "workaround":
      return <SpreadsheetWorkaround />;
  }
}
