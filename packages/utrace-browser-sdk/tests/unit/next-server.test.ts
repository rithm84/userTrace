// @vitest-environment node
/**
 * The Next.js gate on the server, where there is no document: Next.js renders a
 * client component there first, once per request, in one long-lived process.
 */

import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { UTraceGate, utracePreviewStatus } from "../../src/next.js";
import type { BrowserSdkConfig } from "../../src/core/config.js";

const config: BrowserSdkConfig = {
  sdkInstallationId: "shopalytics-preview",
  environmentName: "preview",
  clientReleaseId: "main-8f21",
  schemaVersion: "1",
  protocolVersion: "1",
};

const render = (): string =>
  renderToStaticMarkup(
    createElement(UTraceGate, {
      config,
      profile: "preview",
      pending: createElement("p", null, "preparing"),
      renderFailure: (): ReactNode => createElement("p", null, "blocked"),
      children: createElement("main", null, "product"),
    }),
  );

describe("UTraceGate on the server", () => {
  it("renders the pending surface on every request and never starts activation", async () => {
    // Loaded up front, as in a built server, so a gate started here settles at once.
    await import("../../src/index.js");
    expect(render()).toContain('data-utrace-gate="activating"');
    // A gate started on the server would have failed by the next request.
    await new Promise((resolve) => setTimeout(resolve, 50));
    const later = render();
    expect(later).toContain('data-utrace-gate="activating"');
    expect(later).not.toContain("blocked");
    expect(utracePreviewStatus().phase).toBe("activating");
  });
});
