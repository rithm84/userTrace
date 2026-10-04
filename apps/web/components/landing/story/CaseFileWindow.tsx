"use client";

import { Check, GitPullRequest } from "lucide-react";
import type { JSX } from "react";

import { CaseFileSummary, SUMMARY_ROW_COUNT } from "../casefile/CaseFileSummary";
import { SHOPALYTICS } from "./shopalyticsFixture";
import type { Beat } from "./beats";
import "../casefile/casefile.css";

/**
 * The Case File for the Shopalytics team, beside the one PR it explains. It is the same
 * summary sheet the folder further down opens on, filled in row by row, minus the button,
 * because the PR is already on screen. The team merges.
 */
export function CaseFileWindow(props: Readonly<{ beat: Beat; progress: number }>): JSX.Element {
  const { progress } = props;
  const rows = Math.min(SUMMARY_ROW_COUNT, Math.floor(progress * 8));
  const pr = progress >= 0.6;
  const merge = progress >= 0.8;
  return (
    <div className="st-casefile" data-testid="story-casefile" data-rows={rows}>
      <article className="st-doc">
        <CaseFileSummary rows={rows} onView={null} note={false} />
      </article>
      <aside className={"st-pr" + (pr ? " is-on" : "")} data-testid="story-pr">
        <div className="st-pr-head">
          <GitPullRequest size={18} />
          <span>
            {SHOPALYTICS.implementation.repository} <b>#{SHOPALYTICS.implementation.pullRequest}</b>
          </span>
        </div>
        <h4>Saved conversion views</h4>
        <p>Ready for review · links the Case File · validated on Preview Version {SHOPALYTICS.implementation.repairPreviewVersion}</p>
        <ul>
          {SHOPALYTICS.implementation.checks.map((check) => (
            <li key={check}>
              <Check size={12} strokeWidth={3} />
              {check}
            </li>
          ))}
        </ul>
        <div className={"st-merge" + (merge ? " is-on" : "")} data-testid="story-merge-note">
          <strong>Your team merges.</strong>
          <span>uTrace never merges to production.</span>
        </div>
      </aside>
      {merge ? (
        <span className="st-sfx" data-testid="story-case-closed" aria-label="Case closed">
          Case closed
          <i>!</i>
          <i>!</i>
          <i>!</i>
        </span>
      ) : null}
    </div>
  );
}
