/**
 * The Case File's summary sheet and the row and quote primitives every sheet shares. The
 * story's last window renders the same summary, row by row and without the button, so what
 * the reader sees ship in the story is the thing they can pull open further down.
 */
"use client";

import { CircleCheck, Flag, Lightbulb, Users } from "lucide-react";
import type { ComponentType, JSX, ReactNode } from "react";

import { SHOPALYTICS } from "../story/shopalyticsFixture";

export const CASE_FILE_TITLE = "Saved conversion views";
export const CASE_FILE_EXAMPLE_NOTE = "Example Case File for the Shopalytics scenario; quotes and counts are illustrative.";
const USER = SHOPALYTICS.user.handle.charAt(0).toUpperCase() + SHOPALYTICS.user.handle.slice(1);
const { implementation, validation } = SHOPALYTICS;

export function Row(props: Readonly<{ icon: ComponentType<{ size?: number }>; title: string; children: ReactNode; aside?: ReactNode }>): JSX.Element {
  const Icon = props.icon;
  return (
    <li className="cf-row">
      <span className="cf-row-icon" aria-hidden="true">
        <Icon size={20} />
      </span>
      <div className="cf-row-copy">
        <b>{props.title}</b>
        <div>{props.children}</div>
      </div>
      {props.aside !== undefined ? <div className="cf-row-aside">{props.aside}</div> : null}
    </li>
  );
}

export function Quote(props: Readonly<{ children: ReactNode; by: string; source?: boolean }>): JSX.Element {
  return (
    <figure className={"cf-quote" + (props.source === true ? " cf-quote--source" : "")}>
      <blockquote>{props.children}</blockquote>
      <figcaption>{props.by}</figcaption>
    </figure>
  );
}

const SUMMARY_ROWS: readonly JSX.Element[] = [
  <Row key="need" icon={Users} title="Customer need">
    {USER} rebuilds the same conversion filters every Monday. Leaving the page loses them.
  </Row>,
  <Row key="improvement" icon={Lightbulb} title="Improvement">
    Save the full conversion view by name and reopen it with every filter and the visualization.
  </Row>,
  <Row
    key="validation"
    icon={CircleCheck}
    title="Validation"
    aside={
      <ul className="cf-ticks" aria-label="Validation steps">
        <li>Interview</li>
        <li>First preview, {validation.partial.outcome}</li>
        <li>Repair, {validation.final.outcome}</li>
      </ul>
    }
  >
    {USER} reopened “{SHOPALYTICS.savedViewName}” on Preview Version <code>{implementation.repairPreviewVersion}</code>. All seven filters came back.
  </Row>,
  <Row key="next" icon={Flag} title="Next action">
    Review PR #{implementation.pullRequest} in <code>{implementation.repository}</code>. Your team merges.
  </Row>,
];

export const SUMMARY_ROW_COUNT = SUMMARY_ROWS.length;

/** `onView` wires the View pull request button; null leaves it out, as in the story, where the PR sits beside the sheet. */
export function CaseFileSummary(props: Readonly<{ rows: number; onView: (() => void) | null; note: boolean }>): JSX.Element {
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>Case File</h3>
          <p className="cf-sheet-sub">{CASE_FILE_TITLE}</p>
        </div>
        <span className="cf-stamp" data-testid="casefile-stamp">
          {validation.final.outcome}
        </span>
      </header>
      <p className="cf-byline">
        For the {SHOPALYTICS.client} team · originating user {USER}, {SHOPALYTICS.user.role} at {SHOPALYTICS.account}
      </p>
      <ul className="cf-rows">{SUMMARY_ROWS.slice(0, props.rows)}</ul>
      {props.onView !== null ? (
        <button type="button" className="cf-sheet-button" data-testid="casefile-view-pr" onClick={props.onView}>
          View pull request
        </button>
      ) : null}
      {props.note ? <p className="cf-example-note">{CASE_FILE_EXAMPLE_NOTE}</p> : null}
    </>
  );
}
