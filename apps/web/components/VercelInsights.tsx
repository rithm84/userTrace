"use client";

import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import type { JSX } from "react";

import { redactedPageUrl } from "@/lib/routes";

/** Vercel Web Analytics and Speed Insights, reporting only redacted page URLs. */
export function VercelInsights(): JSX.Element {
  return (
    <>
      <Analytics beforeSend={(event) => ({ ...event, url: redactedPageUrl(event.url) })} />
      <SpeedInsights beforeSend={(event) => ({ ...event, url: redactedPageUrl(event.url) })} />
    </>
  );
}
