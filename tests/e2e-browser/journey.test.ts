/**
 * The cross-package browser journey.
 *
 * Four real components are started together and driven in one real Chrome:
 *
 * - `apps/web` on 3100: the uTrace session tab, against the in-repository
 *   Shopalytics control-plane fixture.
 * - `apps/browser-extension`, unpacked: the packaged Manifest V3 build.
 * - `apps/preview-front-door` on 3200, behind TLS on 8522: the door that
 *   claims the session, routes it, and relays the SDK's activation.
 * - The Shopalytics checkout on 3300 in its preview profile, with
 *   `@utrace/browser-sdk` gating hydration and `@utrace/server-sdk` preloaded
 *   before any application module.
 *
 * Everything those four depend on and do not own — the control plane, browser
 * telemetry ingestion, the Server SDK's control socket, and the OTLP edge — is
 * the contract server the owning workstream wrote, so a disagreement fails
 * their suite as well as this one. Nothing is mocked inside any component.
 *
 * What it proves, in order: the participant compatibility preflight passes, the
 * binding offer reaches the service worker, the browser SDK activates through
 * the real door and exports a baseline and the chart-state milestones, the
 * browser's `traceparent` reaches the Server SDK as a remote parent and
 * produces the `SERVER` span with its `pg` hop, the focused preview tab binds
 * and renders the packaged Rive orb, the session origin advancing to a new
 * runtime reloads the bound tab, signs it in to that runtime, and rebinds it,
 * a point-and-circle scene resolves the channel filter through the SDK's own
 * target registry and applies, pause clears it, and Return to session focuses
 * the session tab.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Page } from "playwright-core";

import {
  startIngest,
  type IngestServer,
} from "../../packages/utrace-browser-sdk/tests/chrome/support/ingest.js";
import { TestControlPlane } from "../../packages/utrace-server-sdk/tests/helpers/control-server.js";
import { TestOtlpReceiver } from "../../packages/utrace-server-sdk/tests/helpers/otlp-receiver.js";
import { ShadowInspector } from "../../apps/browser-extension/tests/chrome/shadow.js";
import { launchChrome, seedInstallation, type JourneyBrowser } from "./support/chrome.js";
import { startControlPlane, type ControlPlane } from "./support/control-plane.js";
import { resignServerManifest } from "./support/server-manifest.js";
import { buildApp, startApp, waitForUrl, type AppSpec, type RunningApp } from "./support/servers.js";
import { startTlsTerminator, type TlsTerminator } from "./support/tls.js";
import { installTrustAnchor, type InstalledTrustAnchor } from "./support/trust-anchor.js";
import {
  ALLOCATION_ID,
  BINDING_ID,
  CANDIDATE_ALLOCATION_ID,
  CANDIDATE_BINDING_ID,
  CANDIDATE_VERSION_ID,
  CLIENT_ID,
  CLIENT_RELEASE_ID,
  INSTALLATION_ID,
  SDK_INSTALLATION_ID,
  SESSION_ID,
  createBrowserSigningIdentity,
  createServerSigningIdentity,
  secrets,
} from "./support/identity.js";

const REPOSITORY_ROOT = resolve(import.meta.dirname, "../..");
const SHOPALYTICS_ROOT = resolve(
  process.env["SHOPALYTICS_ROOT"] ?? resolve(REPOSITORY_ROOT, "../shopalytics"),
);

/** Application ports are the workstream's; every test server is inside 8520-8599. */
const PORTS = {
  web: 3100,
  door: 3200,
  shopalytics: 3300,
  controlPlane: 8520,
  browserIngest: 8521,
  doorTls: 8522,
  serverControl: 8523,
  otlp: 8524,
} as const;

const PREVIEW_APEX = "preview.localtest";
const SESSION_HOST = `e2ebrowser01.${PREVIEW_APEX}`;
const PREVIEW_ORIGIN = `https://${SESSION_HOST}:${String(PORTS.doorTls)}`;
const SESSION_TAB_ORIGIN = `http://localhost:${String(PORTS.web)}`;

const COMMAND_ID = "0199a0c0-0000-7aaa-8000-0000000cd001";
const SCENE_ID = "0199a0c0-0000-7aaa-8000-000000005ce1";
const ACTION_ID = "0199a0c0-0000-7aaa-8000-0000000ac701";

let controlPlane: ControlPlane;
let ingest: IngestServer;
let serverControl: TestControlPlane;
let otlp: TestOtlpReceiver;
let tls: TlsTerminator;
let web: RunningApp;
let door: RunningApp;
let shopalytics: RunningApp;
let shopalyticsSpec: AppSpec;
let bindingId: string = BINDING_ID;
let browser: JourneyBrowser;
let sessionTab: Page;
let previewTab: Page;
let shadow: ShadowInspector;
let documentAttachmentId: string;

/** Released in reverse order by `afterAll`, so a partial start still cleans up. */
const teardown: (() => void | Promise<void>)[] = [];
/** Everything the preview document said, so a stall reports why. */
const previewDiagnostics: string[] = [];

beforeAll(async () => {
  const signing = createBrowserSigningIdentity("utrace-browser-journey");
  const trustAnchor: InstalledTrustAnchor = installTrustAnchor({
    clientRoot: SHOPALYTICS_ROOT,
    keyId: signing.keyId,
    publicKeyB64Url: signing.publicKeyB64Url,
  });
  teardown.push(() => {
    trustAnchor.restore();
  });
  const annotationManifest = readShopalyticsManifest("annotation-manifest.json");
  const serverIdentity = createServerSigningIdentity("shopalytics-journey");

  ingest = await startIngest(PORTS.browserIngest);
  teardown.push(() => ingest.close());
  controlPlane = await startControlPlane({
    port: PORTS.controlPlane,
    signing,
    annotationManifest,
    publicPreviewHost: SESSION_HOST,
    ingestUrl: ingest.url,
    documentOrigin: PREVIEW_ORIGIN,
    upstreamOrigin: `http://127.0.0.1:${String(PORTS.shopalytics)}`,
    applicationPort: PORTS.shopalytics,
  });

  teardown.push(() => controlPlane.close());

  otlp = new TestOtlpReceiver();
  await otlp.listen(PORTS.otlp);
  teardown.push(() => otlp.close());
  serverControl = new TestControlPlane(secrets.serverBootstrapCredential);
  serverControl.useActivation({
    signedManifest: resignServerManifest(readShopalyticsManifest("server-manifest.signed.json"), {
      keyId: serverIdentity.keyId,
      privateKeyPem: serverIdentity.privateKeyPem,
    }),
    previewEntryToken: secrets.previewEntryToken,
    // The dashboard's own data request is the probe: it proves browser CLIENT
    // to server SERVER parentage and the `pg` hop beneath it.
    requiredHops: ["server", "database"],
    probeRouteTemplate: "/api/shopalytics",
    probeMethod: "POST",
  });
  await serverControl.listen(PORTS.serverControl);
  teardown.push(() => serverControl.close());

  const doorSpec: AppSpec = {
    name: "front-door",
    directory: resolve(REPOSITORY_ROOT, "apps/preview-front-door"),
    port: PORTS.door,
    startScript: "start",
    readyUrl: `http://127.0.0.1:${String(PORTS.door)}/`,
    environment: {
      UTRACE_CONTROL_PLANE_BASE_URL: controlPlane.origin,
      UTRACE_CONTROL_PLANE_SERVICE_TOKEN: secrets.controlPlaneServiceToken,
      UTRACE_CONTROL_PLANE_TIMEOUT_MS: "5000",
      UTRACE_CONTROL_PLANE_ATTEMPTS: "2",
      UTRACE_PREVIEW_APEX: PREVIEW_APEX,
      UTRACE_SESSION_COOKIE_NAME: "__Host-utrace-preview-session",
      UTRACE_SESSION_COOKIE_MAX_AGE_SECONDS: "3600",
      UTRACE_UPSTREAM_APPLICATION_PORT: String(PORTS.shopalytics),
      UTRACE_ROUTE_CONTEXT_TTL_SECONDS: "120",
    },
  };

  shopalyticsSpec = {
    name: "shopalytics",
    directory: SHOPALYTICS_ROOT,
    port: PORTS.shopalytics,
    startScript: "start:preview",
    readyUrl: `http://127.0.0.1:${String(PORTS.shopalytics)}/api/health`,
    environment: {
      ...readShopalyticsEnvironment(),
      UTRACE_PROFILE: "preview",
      NEXT_PUBLIC_UTRACE_PROFILE: "preview",
      NEXT_PUBLIC_UTRACE_SDK_INSTALLATION_ID: SDK_INSTALLATION_ID,
      NEXT_PUBLIC_UTRACE_CLIENT_RELEASE_ID: CLIENT_RELEASE_ID,
      UTRACE_PREVIEW_HANDOFF_SECRET: secrets.previewHandoffSecret,
      UTRACE_PREVIEW_ROUTE_SIGNING_SECRET: secrets.routeContextSigningSecret,
      UTRACE_SERVER_BOOTSTRAP_CREDENTIAL: secrets.serverBootstrapCredential,
      UTRACE_SERVER_CONTROL_URL: `ws://127.0.0.1:${String(PORTS.serverControl)}`,
      UTRACE_SERVER_INGEST_URL: `http://127.0.0.1:${String(PORTS.otlp)}`,
      UTRACE_SERVER_SERVICE_NAME: "shopalytics_web",
      UTRACE_SERVER_MANIFEST_TRUST_ANCHOR: serverIdentity.trustAnchorEnvValue,
    },
  };

  buildApp(doorSpec);
  buildApp(shopalyticsSpec);

  // The session tab inlines the extension id at build time, so the extension is
  // launched first and the web application is built against the id Chrome
  // assigned it.
  browser = await launchChrome({
    extensionDirectory: resolve(REPOSITORY_ROOT, "apps/browser-extension/dist-e2e-browser"),
    previewApex: PREVIEW_APEX,
  });
  teardown.push(() => browser.close());
  await seedInstallation(browser.worker, INSTALLATION_ID);

  const webSpec: AppSpec = {
    name: "web",
    directory: resolve(REPOSITORY_ROOT, "apps/web"),
    port: PORTS.web,
    startScript: "start",
    readyUrl: `${SESSION_TAB_ORIGIN}/`,
    environment: {
      UTRACE_CONTROL_PLANE_FIXTURES: "1",
      UTRACE_PARTICIPANT_FIXTURE: "1",
      NEXT_PUBLIC_SUPABASE_URL: `http://127.0.0.1:${String(PORTS.controlPlane)}`,
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "journey-publishable-key",
      NEXT_PUBLIC_UTRACE_EXTENSION_ID: browser.extensionId,
      UTRACE_API_BASE_URL: controlPlane.origin,
      UTRACE_CLIENT_ID: "shopalytics",
    },
  };
  buildApp(webSpec);

  door = await startApp(doorSpec);
  teardown.push(() => door.close());
  tls = await startTlsTerminator({
    port: PORTS.doorTls,
    apex: PREVIEW_APEX,
    upstreamPort: PORTS.door,
  });
  teardown.push(() => tls.close());
  shopalytics = await startApp(shopalyticsSpec);
  teardown.push(() => shopalytics.close());
  web = await startApp(webSpec);
  teardown.push(() => web.close());
  await waitForUrl(`${SESSION_TAB_ORIGIN}/sessions/${SESSION_ID}`, 60_000);

  sessionTab = await browser.context.newPage();
  await sessionTab.goto(`${SESSION_TAB_ORIGIN}/sessions/${SESSION_ID}`, { waitUntil: "load" });
}, 900_000);

afterAll(async () => {
  // Reverse order, and only what actually started: a failure part-way through
  // `beforeAll` must still release every port and restore the trust anchor.
  for (const release of [...teardown].reverse()) {
    await Promise.resolve(release()).catch((error: unknown) => {
      process.stderr.write(`[journey] teardown failed: ${String(error)}\n`);
    });
  }
  teardown.length = 0;
});

describe("participant setup", () => {
  it("passes the compatibility preflight against the packaged extension", async () => {
    const result = await callExtension(sessionTab, {
      type: "utrace_preflight_request",
      protocol_version: "1",
    });
    expect(result["type"]).toBe("utrace_preflight_result");
    expect(result["account_linked"]).toBe(true);
    expect(result["host_permission"]).toBe(true);
    expect(String(result["installation_proof"])).toMatch(/^[A-Za-z0-9_-]{32,}$/u);
  });

  it("delivers the session's binding offer to the service worker", async () => {
    const answer = await callExtension(sessionTab, {
      type: "utrace_binding_offer",
      session_id: SESSION_ID,
      binding_offer: {
        schema_version: 1,
        offer_token: "am91cm5leV9iaW5kaW5nX29mZmVyX3Rva2Vu",
        session_id: SESSION_ID,
        client_id: CLIENT_ID,
        extension_installation_id: INSTALLATION_ID,
        preview_runtime_allocation_id: ALLOCATION_ID,
        protocol_version: "1.0",
        expires_at: "2126-09-20T17:06:04Z",
      },
    });
    expect(answer).toStrictEqual({ type: "utrace_binding_offer_ack", accepted: true });

    const recovered = await controlPlane.waitForExtensionFrame("recovery_request", () => true);
    expect(recovered.payload["installation_id"]).toBe(INSTALLATION_ID);
  });
});

describe("the preview", () => {
  it("claims the session origin and signs the growth lead in", async () => {
    previewTab = await browser.context.newPage();
    previewTab.on("console", (message) => {
      previewDiagnostics.push(`console.${message.type()}: ${message.text()}`);
    });
    previewTab.on("pageerror", (error: Error) => {
      previewDiagnostics.push(`pageerror: ${error.message}`);
    });
    previewTab.on("requestfailed", (request) => {
      previewDiagnostics.push(
        `requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText ?? ""}`,
      );
    });
    // The claim admits the browser to the origin, and the door's product sign-in
    // then signs the growth lead in to the routed runtime.
    await previewTab.goto(
      `${PREVIEW_ORIGIN}/__utrace/claim?code=${encodeURIComponent(secrets.handoffCode)}`,
      { waitUntil: "load" },
    );
    const landed = new URL(previewTab.url());
    expect(landed.origin).toBe(PREVIEW_ORIGIN);
    expect(landed.pathname.startsWith("/__utrace/")).toBe(false);
  });

  it("activates the browser SDK through the real front door and renders the dashboard", async () => {
    await previewTab.goto(`${PREVIEW_ORIGIN}/app/conversion`, { waitUntil: "load" });
    try {
      await previewTab.waitForSelector('[data-testid="conversion-dashboard"]', { timeout: 120_000 });
    } catch (cause) {
      throw new Error(describeStalledPreview(await readGateState()), { cause });
    }

    expect(controlPlane.activationCount()).toBeGreaterThan(0);
    expect(ingest.credentialsSeen).toContain(secrets.telemetryToken);
    await waitUntil("the runtime exports its initial baseline", () =>
      ingest.observations.some((record) => record.observation_type === "baseline.snapshot"),
    );

  });

  it("exports the chart-state milestones the scenario requires", async () => {
    for (const milestone of [
      "conversion_filters_applied",
      "conversion_series_displayed",
      "conversion_render_completed",
    ]) {
      await waitUntil(`the ${milestone} milestone reaches ingestion`, () =>
        ingest.observations.some(
          (record) =>
            record.observation_type === "product.milestone" &&
            record.payload["milestone_id"] === milestone,
        ),
      );
    }
  });

  it("joins the browser's request to the Server SDK's SERVER span and its pg hop", async () => {
    await waitUntil(
      "the Server SDK exports the dashboard query",
      () =>
        otlp.batches.some((batch) =>
          batch.spans.some((span) => span.name === "POST /api/shopalytics"),
        ),
      120_000,
    );

    const serverSpan = otlp.batches
      .flatMap((batch) => batch.spans)
      .find((span) => span.name === "POST /api/shopalytics");
    expect(serverSpan, "one authoritative SERVER span for the dashboard query").toBeDefined();

    const browserRequests = ingest.observations.filter(
      (record) => record.observation_type === "request.started" && record.trace_id !== undefined,
    );
    expect(browserRequests.length, "the browser SDK observed its own data requests").toBeGreaterThan(
      0,
    );
    // One causal product operation: the server span continues the browser's
    // trace, which only happens when the door's preview entry token matched.
    expect(browserRequests.map((record) => record.trace_id)).toContain(serverSpan?.traceId);

    const databaseSpan = otlp.batches
      .flatMap((batch) => batch.spans)
      .find(
        (span) => span.traceId === serverSpan?.traceId && span.attributes["db.system.name"] !== undefined,
      );
    expect(databaseSpan, "the pg hop beneath the server span").toBeDefined();
  });
});

describe("the bound surface", () => {
  it("binds the focused preview tab and renders the packaged character", async () => {
    // The preview tab was already the active one while the dashboard loaded,
    // so focusing it again changes nothing. The participant's own path is to
    // look at the session tab and then turn to the preview; that is what makes
    // the service worker probe a document the SDK has since activated.
    await sessionTab.bringToFront();
    await previewTab.bringToFront();
    const submitted = await controlPlane.waitForExtensionFrame("binding_offer", () => true);
    expect(submitted.payload["document_origin"]).toBe(PREVIEW_ORIGIN);

    // The attachment the control plane confirms has to be the one the
    // extension probed, which is the one inside the signed page proof: the
    // preview served more than one document, and a binding for the wrong one
    // is refused as a document attachment mismatch.
    documentAttachmentId = readPageProofClaim(submitted.payload, "document_attachment_id");
    expect(documentAttachmentId).toMatch(/^[0-9a-f-]{36}$/u);

    controlPlane.sendToExtension({
      kind: "binding_state",
      payload: {
        status: "bound",
        session_id: SESSION_ID,
        client_id: CLIENT_ID,
        extension_binding_id: BINDING_ID,
        document_attachment_id: documentAttachmentId,
        preview_runtime_allocation_id: ALLOCATION_ID,
        binding_revision: 1,
      },
    });

    shadow = await ShadowInspector.create(previewTab);
    await waitUntil("the orb appears in the bound document", () => shadow.exists("orb"), 60_000);

    controlPlane.sendToExtension({
      kind: "character_state",
      payload: {
        character_revision: 41,
        session_id: SESSION_ID,
        state: "speaking",
        gaze_x: -0.2,
        gaze_y: 0.1,
        speech_energy: 0.72,
        motion_scale: 1,
      },
    });
    await waitUntil(
      "the character applies the speaking revision",
      async () => (await shadow.read("orb-canvas", "this.dataset.utraceState")) === "speaking",
      60_000,
    );
    // The packaged Rive renderer must actually paint, not merely mount.
    const painted = await shadow.read(
      "orb-canvas",
      "(() => { const context = this.getContext('2d'); if (context === null) { return 'no-2d-context'; } const { data } = context.getImageData(0, 0, this.width, this.height); let opaque = 0; for (let index = 3; index < data.length; index += 4) { if (data[index] > 0) { opaque += 1; } } return String(opaque); })()",
    );
    expect(Number(painted)).toBeGreaterThan(200);
  });

  it("reloads the bound tab onto a new version, signs it in again, and rebinds it", async () => {
    // A candidate is a new runtime with its own product sign-in secret, so the
    // product session the browser already holds is not valid there.
    await shopalytics.close();
    shopalytics = await startApp({
      ...shopalyticsSpec,
      environment: {
        ...shopalyticsSpec.environment,
        UTRACE_PREVIEW_HANDOFF_SECRET: secrets.candidateHandoffSecret,
      },
    });
    controlPlane.advance({
      allocationId: CANDIDATE_ALLOCATION_ID,
      previewVersionId: CANDIDATE_VERSION_ID,
      routeRevision: 2,
      handoffSecret: secrets.candidateHandoffSecret,
    });

    // The session tab hands the extension a fresh offer for the new runtime.
    const answer = await callExtension(sessionTab, {
      type: "utrace_binding_offer",
      session_id: SESSION_ID,
      binding_offer: {
        schema_version: 1,
        offer_token: "am91cm5leV9jYW5kaWRhdGVfb2ZmZXJfdG9rZW4",
        session_id: SESSION_ID,
        client_id: CLIENT_ID,
        extension_installation_id: INSTALLATION_ID,
        preview_runtime_allocation_id: CANDIDATE_ALLOCATION_ID,
        protocol_version: "1.0",
        expires_at: "2126-09-20T17:06:04Z",
      },
    });
    expect(answer).toStrictEqual({ type: "utrace_binding_offer_ack", accepted: true });

    // Nobody touches the preview tab: the extension reloads it, the door signs it
    // in to the new runtime, and the reloaded document asks to rebind.
    const rebind = await controlPlane.waitForExtensionFrame(
      "binding_offer",
      (payload) =>
        readPageProofClaim(payload, "preview_runtime_allocation_id") === CANDIDATE_ALLOCATION_ID,
    );
    expect((rebind.payload["request"] as { kind?: string }).kind).toBe("rebind");
    try {
      await previewTab.waitForSelector('[data-testid="conversion-dashboard"]', { timeout: 60_000 });
    } catch (cause) {
      throw new Error(describeStalledPreview(await readGateState()), { cause });
    }
    expect(new URL(previewTab.url()).pathname).toBe("/app/conversion");

    documentAttachmentId = readPageProofClaim(rebind.payload, "document_attachment_id");
    bindingId = CANDIDATE_BINDING_ID;
    controlPlane.sendToExtension({
      kind: "binding_state",
      payload: {
        status: "bound",
        session_id: SESSION_ID,
        client_id: CLIENT_ID,
        extension_binding_id: bindingId,
        document_attachment_id: documentAttachmentId,
        preview_runtime_allocation_id: CANDIDATE_ALLOCATION_ID,
        binding_revision: 2,
      },
    });
    shadow = await ShadowInspector.create(previewTab);
    await waitUntil("the orb returns in the new document", () => shadow.exists("orb"), 60_000);
  });

  it("draws a point-and-circle scene on the channel filter and clears it on pause", async () => {
    const target = requireRegisteredTarget("conversion_filter_channel");

    controlPlane.sendToExtension({
      kind: "readiness",
      payload: {
        readiness_revision: 9,
        session_id: SESSION_ID,
        extension_readiness: "ready",
        combined_readiness: "ready",
        blockers: [],
      },
    });
    controlPlane.sendToExtension({
      kind: "visual_command",
      payload: {
        command_id: COMMAND_ID,
        client_id: CLIENT_ID,
        session_id: SESSION_ID,
        extension_binding_id: bindingId,
        document_attachment_id: documentAttachmentId,
        expected_lifecycle_revision: 1,
        deadline_at: new Date(Date.now() + 30_000).toISOString(),
        command: {
          type: "scene",
          scene: {
            scene_id: SCENE_ID,
            steps: [
              { step: "move", target_id: target.target_id, target_revision: target.target_revision },
              {
                step: "point",
                target_id: target.target_id,
                target_revision: target.target_revision,
                dwell_ms: 100,
              },
              {
                step: "circle",
                target_id: target.target_id,
                target_revision: target.target_revision,
                retain: true,
              },
            ],
          },
        },
      },
    });

    const result = await controlPlane.waitForExtensionFrame(
      "command_result",
      (payload) => payload["command_id"] === COMMAND_ID,
    );
    expect(
      result.payload["outcome"],
      `the scene was refused: ${JSON.stringify(result.payload["reason"] ?? null)}`,
    ).toBe("applied");
    expect(await shadow.count("scene-mark")).toBeGreaterThan(0);

    // The participant pauses from the orb, which is the only way a pause
    // reaches the control plane: the extension asks, and clears the scene only
    // once the authoritative transition comes back.
    await expandControls();
    const pause = await shadow.centre("pill-pause");
    await previewTab.mouse.move(pause.x, pause.y);
    await previewTab.mouse.click(pause.x, pause.y);

    const requested = await controlPlane.waitForExtensionFrame(
      "control_action",
      (payload) => payload["action"] === "pause",
    );
    expect(requested.payload["session_id"]).toBe(SESSION_ID);
    expect(requested.payload["locally_confirmed"]).toBe(false);

    controlPlane.sendToExtension({
      kind: "control_action_result",
      payload: {
        action_id: ACTION_ID,
        session_id: SESSION_ID,
        disposition: "accepted",
        session_control_revision: 6,
        mute_state: "unmuted",
        pause_state: "paused",
      },
    });
    await waitUntil("pause clears the scene", async () => (await shadow.count("scene-mark")) === 0);
  });

  it("focuses the session tab from Return to session", async () => {
    const pill = await shadow.centre("pill-return");
    await previewTab.mouse.move(pill.x, pill.y);
    await previewTab.mouse.click(pill.x, pill.y);
    await waitUntil(
      "the session tab is the visible one",
      async () => (await sessionTab.evaluate(() => document.visibilityState)) === "visible",
      30_000,
    );
  });
});

/** Hovers the orb and waits for the four control pills to finish travelling out. */
async function expandControls(): Promise<void> {
  const centre = await shadow.centre("orb");
  await previewTab.mouse.move(centre.x, centre.y);
  await waitUntil(
    "the controls expand",
    async () => (await shadow.read("orb-root", "this.dataset.expanded")) === "true",
    30_000,
  );
  await waitUntil(
    "the pills settle outside the orb",
    async () => {
      const orb = await shadow.box("orb");
      const above = await shadow.box("pill-return");
      return above.y + above.height < orb.y;
    },
    30_000,
  );
}

/** Sends one externally connectable message from the page, as the session tab does. */
async function callExtension(
  page: Page,
  message: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return page.evaluate(
    async ([extensionId, body]: readonly [string, Record<string, unknown>]) =>
      await new Promise<Record<string, unknown>>((resolveAnswer) => {
        chrome.runtime.sendMessage(extensionId, body, (answer: unknown) => {
          resolveAnswer((answer ?? {}) as Record<string, unknown>);
        });
      }),
    [browser.extensionId, message] as const,
  );
}

/** The registry change the SDK exported for one annotated definition. */
function requireRegisteredTarget(definitionId: string): {
  readonly target_id: string;
  readonly target_revision: number;
} {
  for (const record of [...ingest.observations].reverse()) {
    if (record.observation_type !== "target.registry_change") continue;
    const target = record.payload["target"] as
      | { definition_id?: string; target_id?: string; target_revision?: number }
      | null;
    if (target?.definition_id !== definitionId) continue;
    if (typeof target.target_id !== "string" || typeof target.target_revision !== "number") continue;
    return { target_id: target.target_id, target_revision: target.target_revision };
  }
  throw new Error(
    `the browser SDK never registered a target for "${definitionId}"; it registered ${[
      ...new Set(
        ingest.observations
          .filter((record) => record.observation_type === "target.registry_change")
          .map(
            (record) =>
              (record.payload["target"] as { definition_id?: string } | null)?.definition_id ??
              "auto",
          ),
      ),
    ].join(", ")}`,
  );
}

/** Reads one claim out of the signed page proof an extension frame carried. */
function readPageProofClaim(payload: Record<string, unknown>, claim: string): string {
  const proof = payload["page_proof"] as { payload_b64url?: string } | undefined;
  if (proof?.payload_b64url === undefined) {
    throw new Error("the extension frame carried no page proof");
  }
  const claims = JSON.parse(
    Buffer.from(proof.payload_b64url, "base64url").toString("utf8"),
  ) as Record<string, unknown>;
  const value = claims[claim];
  if (typeof value !== "string") {
    throw new Error(`the page proof carries no ${claim}`);
  }
  return value;
}

/** Polls until `predicate` holds, then reports what it was waiting for. */
async function waitUntil(
  description: string,
  predicate: () => boolean | Promise<boolean>,
  timeoutMs = 60_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise<void>((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`timed out waiting for ${description}`);
}

/** What the activation gate is showing, and why, when the dashboard stalls. */
async function readGateState(): Promise<string> {
  return previewTab.evaluate(() => {
    const gate = document.querySelector("[data-utrace-gate]");
    const blocked = document.querySelector('[data-testid="utrace-preview-blocked"]');
    return JSON.stringify({
      gate: gate?.getAttribute("data-utrace-gate") ?? "absent",
      blocked: blocked?.textContent ?? null,
      title: document.title,
      body: document.body.innerText.slice(0, 600),
    });
  });
}

function describeStalledPreview(gateState: string): string {
  return [
    "the Shopalytics preview never rendered the conversion dashboard",
    `gate: ${gateState}`,
    `activations: ${String(controlPlane.activationCount())}`,
    `ingest credentials: ${String(ingest.credentialsSeen.length)}`,
    `observations: ${ingest.observations.map((record) => record.observation_type).join(", ")}`,
    ...previewDiagnostics.slice(-40),
  ].join("\n");
}

/** Reads a manifest `npm run utrace:manifest` produced in the client checkout. */
function readShopalyticsManifest(file: string): Record<string, unknown> {
  return JSON.parse(
    readFileSync(resolve(SHOPALYTICS_ROOT, "build/utrace", file), "utf8"),
  ) as Record<string, unknown>;
}

/** Reads the client checkout's own `.env.local`, which holds its database. */
function readShopalyticsEnvironment(): Record<string, string> {
  const contents = readFileSync(resolve(SHOPALYTICS_ROOT, ".env.local"), "utf8");
  const values: Record<string, string> = {};
  for (const line of contents.split("\n")) {
    const match = /^([A-Z0-9_]+)=(.*)$/u.exec(line.trim());
    if (match !== null) values[match[1] ?? ""] = match[2] ?? "";
  }
  return values;
}
