import { describe, expect, test } from "vitest";

import { redactedPageUrl } from "@/lib/routes";

describe("page URLs reported to Vercel", () => {
  test("an invitation token never leaves the site", () => {
    expect(redactedPageUrl("https://utrace.dev/invitations/secret-bearer-token?from=discord")).toBe(
      "https://utrace.dev/invitations/[token]",
    );
  });

  test("identifiers become route parameters and deeper segments stay", () => {
    expect(redactedPageUrl("https://utrace.dev/dashboard/cases/01a0-signal/evidence#top")).toBe(
      "https://utrace.dev/dashboard/cases/[productSignalId]/evidence",
    );
    expect(redactedPageUrl("https://utrace.dev/sessions/01a1-session")).toBe(
      "https://utrace.dev/sessions/[sessionId]",
    );
  });

  test("query strings such as OAuth codes and return paths are dropped", () => {
    expect(redactedPageUrl("https://utrace.dev/extension/link?code_challenge=abc&state=xyz")).toBe(
      "https://utrace.dev/extension/link",
    );
    expect(redactedPageUrl("https://utrace.dev/sign-in?next=%2Fdashboard")).toBe("https://utrace.dev/sign-in");
  });
});
