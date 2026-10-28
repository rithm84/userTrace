/** What the team gets: the illustrative Case File as a folder of sheets the reader can pull. */
"use client";

import type { JSX } from "react";

import { useReducedMotion } from "@/components/agent/useReducedMotion";

import { CaseFileFolder } from "./CaseFileFolder";
import "./casefile.css";

export function CaseFileSection(): JSX.Element {
  const reducedMotion = useReducedMotion();
  return (
    <section className="cf-section" aria-labelledby="casefile-title" data-testid="landing-casefile" data-reduced-motion={reducedMotion} id="what-you-get">
      <div className="lp-wrap cf-layout">
        <div className="cf-intro">
          <p className="lp-kicker">For your team</p>
          <h2 className="lp-h2" id="casefile-title">
            One case file.
            <br />
            Every reason.
          </h2>
          <p className="lp-lede">The Case File is engineer-credible: customer pain and product impact first, then the trace, the decision, the change, the risk and the next action.</p>
          <p className="cf-invitation">Click a divider to read each section.</p>
        </div>
        <CaseFileFolder reducedMotion={reducedMotion} />
      </div>
    </section>
  );
}
