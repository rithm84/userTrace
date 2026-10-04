/**
 * The Next.js App Router helper.
 *
 * The gate withholds the application subtree until the runtime is ready, never
 * renders the application unobserved after a failure, and in the production
 * profile carries no runtime at all — only the inert annotation surface.
 */

import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { UTraceGate, utracePreviewStatus } from "../../src/next.js";
import { inertAnnotationSurface } from "../../src/annotations/api.js";
import type { BrowserSdkConfig } from "../../src/core/config.js";

const config: BrowserSdkConfig = {
  sdkInstallationId: "shopalytics-preview",
  environmentName: "preview",
  clientReleaseId: "main-8f21",
  schemaVersion: "1",
  protocolVersion: "1",
};

describe("UTraceGate", () => {
  it("renders the application directly in the production profile", () => {
    const markup = renderToStaticMarkup(
      createElement(
        UTraceGate,
        {
          config,
          profile: "production",
          pending: createElement("p", null, "preparing"),
          renderFailure: (): ReactNode => createElement("p", null, "blocked"),
          children: createElement("main", null, "product"),
        },
      ),
    );
    expect(markup).toContain('data-utrace-gate="ready"');
    expect(markup).toContain("product");
  });

  it("withholds the application while the gate runs in the preview profile", () => {
    const markup = renderToStaticMarkup(
      createElement(
        UTraceGate,
        {
          config,
          profile: "preview",
          pending: createElement("p", null, "preparing"),
          renderFailure: (): ReactNode => createElement("p", null, "blocked"),
          children: createElement("main", null, "product"),
        },
      ),
    );
    expect(markup).toContain('data-utrace-gate="activating"');
    expect(markup).toContain("preparing");
    expect(markup).not.toContain("product");
    expect(utracePreviewStatus().phase).not.toBe("ready");
  });
});

describe("inert annotation surface", () => {
  it("grants no scope and produces no observation", () => {
    const surface = inertAnnotationSurface();
    expect(surface.registerTarget({ element: document.createElement("button"), definitionId: "x" })).toEqual({
      accepted: false,
      reason: "runtime_not_ready",
    });
    expect(surface.safeValue("safe.view_name", "anything")).toEqual({
      accepted: false,
      reason: "runtime_not_ready",
    });
    expect(
      surface.milestone({
        milestoneId: "saved_view_created",
        severity: null,
        priority: null,
        entityDefinitionId: null,
        entityInstanceRef: null,
        attributes: [],
      }),
    ).toEqual({ accepted: false, reason: "runtime_not_ready" });
    surface.markSensitive(document.createElement("div"));
    surface.setActor("agent");
    expect(document.body.getAttribute("data-utrace-sensitive")).toBeNull();
  });
});
