/** The seven sheets in the Case File folder, every figure read from the shared Shopalytics fixture. */
"use client";

import { AlertTriangle, CircleCheck, ClipboardCheck, Code2, Flag, GitPullRequest, Link2, MessagesSquare, ShieldCheck, Wrench } from "lucide-react";
import type { ComponentType, JSX } from "react";

import { SHOPALYTICS } from "../story/shopalyticsFixture";
import { CaseFileSummary, Quote, Row, SUMMARY_ROW_COUNT } from "./CaseFileSummary";

const USER = SHOPALYTICS.user.handle.charAt(0).toUpperCase() + SHOPALYTICS.user.handle.slice(1);
const { implementation, validation } = SHOPALYTICS;

export type SheetId = "summary" | "evidence" | "frequency" | "trace" | "change" | "risk" | "next";

export type SheetBodyProps = Readonly<{ show: (id: SheetId) => void }>;

export type CaseFileSheet = Readonly<{
  id: SheetId;
  /** The word on the index divider. */
  tab: string;
  title: string;
  Body: ComponentType<SheetBodyProps>;
}>;

function Summary(props: SheetBodyProps): JSX.Element {
  return <CaseFileSummary rows={SUMMARY_ROW_COUNT} onView={() => props.show("change")} note />;
}

function Evidence(): JSX.Element {
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>Customer evidence</h3>
          <p className="cf-sheet-sub">The Monday filter stack, rebuilt by hand.</p>
        </div>
      </header>
      <Quote source by={`${USER} · ${SHOPALYTICS.account} · Discord ${SHOPALYTICS.request.channel} · ${SHOPALYTICS.request.time}`}>
        {SHOPALYTICS.request.text}
      </Quote>
      <ul className="cf-rows">
        <Row icon={MessagesSquare} title="Interview">
          “The same conversion filters. Every single week.” Then, on the first candidate: “The date range and channel came back. Sizing didn&apos;t.”
        </Row>
        <Row icon={AlertTriangle} title="Impact">
          Every Monday&apos;s conversion analysis starts from zero.
        </Row>
        <Row icon={Wrench} title="Workaround">
          Rebuild the date range, traffic, audience, reviews, product and metric filters by hand.
        </Row>
      </ul>
    </>
  );
}

function Frequency(): JSX.Element {
  const frequency = SHOPALYTICS.frequency;
  const stats = [
    ["People", frequency.people],
    ["Accounts", frequency.accounts],
    ["Reports", frequency.reports],
    ["First seen", frequency.firstSeen],
    ["Last seen", frequency.lastSeen],
  ] as const;
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>Frequency</h3>
          <p className="cf-sheet-sub">The same job, heard again.</p>
        </div>
      </header>
      <dl className="cf-stats">
        {stats.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {SHOPALYTICS.corroboration.map((report) => (
        <Quote key={report.kind} by={report.byline}>
          {report.quote}
        </Quote>
      ))}
      <p className="cf-note">A separate projection, updated by later corroboration. The evidence, decisions and validation above stay fixed, and {USER} stays the originating user.</p>
    </>
  );
}

function Trace(): JSX.Element {
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>Product trace</h3>
          <p className="cf-sheet-sub">From the filter bar to the reopened view.</p>
        </div>
      </header>
      <ul className="cf-rows">
        <Row icon={Link2} title="Route">
          <code>{SHOPALYTICS.trace.route}</code>, watching the {SHOPALYTICS.trace.targets.join(" and the ")}.
        </Row>
        <Row icon={Code2} title="Spans, in workflow order">
          <ol className="cf-spans">
            {SHOPALYTICS.trace.spans.map((span) => (
              <li key={span}>
                <code>{span}</code>
              </li>
            ))}
          </ol>
        </Row>
        <Row icon={ShieldCheck} title="Errors">
          {SHOPALYTICS.trace.errors}. A successful request still has to bring the complete saved state back.
        </Row>
        <Row icon={CircleCheck} title={`Restored on ${implementation.repairPreviewVersion}`}>
          <span className="cf-chips">
            {SHOPALYTICS.filters.map((filter) => (
              <span className="cf-chip" key={filter.criterion}>
                {filter.label}
              </span>
            ))}
          </span>
        </Row>
      </ul>
    </>
  );
}

function Change(): JSX.Element {
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>The change</h3>
          <p className="cf-sheet-sub">One bounded pull request, validated before it was opened.</p>
        </div>
        <span className="cf-chip">Approved · live</span>
      </header>
      <ul className="cf-rows">
        <Row icon={ClipboardCheck} title="Implementation Gate">
          Live implementation approved for a bounded saved-view workflow. Out of scope: {SHOPALYTICS.gate.excluded.join(", ").toLowerCase()}.
        </Row>
        <Row icon={GitPullRequest} title={`Pull request #${implementation.pullRequest}`}>
          <code>{implementation.repository}</code> on <code>{implementation.branch}</code>, {implementation.filesTouched} files. Checks: {implementation.checks.join(", ")}, all passed.
        </Row>
        <Row icon={CircleCheck} title="Preview Versions">
          <code>{implementation.previewVersion}</code> {validation.partial.outcome}: the review topic was not restored, so the evidence went back through the Implementation Gate. <code>{implementation.repairPreviewVersion}</code> {validation.final.outcome} by the same user.
        </Row>
      </ul>
    </>
  );
}

function Risk(): JSX.Element {
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>Risk</h3>
          <p className="cf-sheet-sub">A bounded change, with checks still to own.</p>
        </div>
      </header>
      <ul className="cf-rows">
        <Row icon={AlertTriangle} title="Blast radius">
          {SHOPALYTICS.risk.blastRadius}
        </Row>
        <Row icon={ShieldCheck} title="Assumptions">
          {SHOPALYTICS.risk.assumptions.join(" ")}
        </Row>
        <Row icon={ClipboardCheck} title="Follow-up checks">
          {SHOPALYTICS.risk.followUps.join(" ")}
        </Row>
      </ul>
    </>
  );
}

function Next(): JSX.Element {
  return (
    <>
      <header className="cf-sheet-head">
        <div>
          <h3>Next action</h3>
          <p className="cf-sheet-sub">Review and merge PR #{implementation.pullRequest}.</p>
        </div>
      </header>
      <ul className="cf-rows">
        <Row icon={Flag} title="Your team owns the merge">
          uTrace delivers the Case File and the validated change. It never merges to production.
        </Row>
        <Row icon={ClipboardCheck} title="Review with the evidence in hand">
          The customer evidence, the validated change and the remaining checks, all in <code>{implementation.repository}</code>.
        </Row>
        <Row icon={ShieldCheck} title="If it is not merged">
          Nothing changes in production. The Case File stays published with its evidence, decision and validation.
        </Row>
      </ul>
    </>
  );
}

export const CASE_FILE_SHEETS: readonly CaseFileSheet[] = [
  { id: "summary", tab: "Summary", title: "Case File", Body: Summary },
  { id: "evidence", tab: "Evidence", title: "Customer evidence", Body: Evidence },
  { id: "frequency", tab: "Frequency", title: "Frequency", Body: Frequency },
  { id: "trace", tab: "Trace", title: "Product trace", Body: Trace },
  { id: "change", tab: "Change", title: "The change", Body: Change },
  { id: "risk", tab: "Risk", title: "Risk", Body: Risk },
  { id: "next", tab: "Next", title: "Next action", Body: Next },
];

export function caseFileSheet(id: SheetId): CaseFileSheet {
  const found = CASE_FILE_SHEETS.find((sheet) => sheet.id === id);
  if (found === undefined) throw new Error(`Unknown Case File sheet ${id}.`);
  return found;
}
