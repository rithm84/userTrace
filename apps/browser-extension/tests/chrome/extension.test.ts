/**
 * The packaged extension driven in the installed Google Chrome.
 *
 * It proves the journey end to end against test doubles of the two contracts
 * other workstreams own: the control plane's `WS /v1/extension/control` and the
 * embedded browser SDK's main-world bridge. Nothing here is mocked inside the
 * extension — the real unpacked build runs, injects its real surface, and
 * renders the packaged Rive character.
 */
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Page } from "playwright-core";

import { ORB_KEY } from "../../src/background/storage";
import { ORB_LAYOUT } from "../../src/content/orbGeometry";
import { SCHEMA_VERSION, readPageProofClaims } from "../../src/protocol/common";
import { DEFAULT_ORB_SETTINGS } from "../../src/shared/portMessages";
import {
  ATTACHMENT_ID,
  ALLOCATION_ID,
  BINDING_ID,
  CLIENT_ID,
  INSTALLATION_ID,
  SESSION_ID,
  bindingOffer,
  launchLinkedChrome,
  openPage,
  startControlPlane,
  startStaticServer,
  waitUntil,
  type Browser,
  type ControlPlane,
} from "./harness";
import { ShadowInspector } from "./shadow";

const COMMAND_ID = "0199a0c0-0000-7aaa-8000-0000000cd001";
const SCENE_ID = "0199a0c0-0000-7aaa-8000-000000005ce1";
const ACTION_ID = "0199a0c0-0000-7aaa-8000-0000000ac701";

let control: ControlPlane;
let statics: Server;
let browser: Browser;
let preview: Page;
let session: Page;
let shadow: ShadowInspector;

beforeAll(async () => {
  control = await startControlPlane();
  statics = await startStaticServer();
  browser = await launchLinkedChrome(INSTALLATION_ID);

  preview = await openPage(browser.context, "/preview.html");
  session = await openPage(browser.context, "/session.html");
}, 120_000);

afterAll(async () => {
  await browser.close();
  await control.close();
  await new Promise<void>((done) => {
    statics.close(() => {
      done();
    });
  });
});

/** Hovers the orb and waits for the four pills to finish travelling outwards. */
async function expandControls(): Promise<void> {
  const centre = await shadow.centre("orb");
  await preview.mouse.move(centre.x, centre.y);
  await waitUntil(
    "the controls expand",
    async () => (await shadow.read("orb-root", "this.dataset.expanded")) === "true",
  );
  await waitUntil("the pills settle outside the orb", async () => {
    const orb = await shadow.box("orb");
    const above = await shadow.box("pill-return");
    if (above.y + above.height >= orb.y) {
      return false;
    }
    for (const pill of ["pill-return", "pill-mute", "pill-pause", "pill-end"]) {
      if ((await shadow.read(pill, "this.getAnimations().length")) !== "0") {
        return false;
      }
    }
    return true;
  });
}

/** Reads the element's viewport rectangle as `[left, top, width, height]`. */
async function rect(testId: string): Promise<readonly number[]> {
  const value = await shadow.read(
    testId,
    "(() => { const r = this.getBoundingClientRect(); return JSON.stringify([r.left, r.top, r.width, r.height]); })()",
  );
  return JSON.parse(value ?? "[]") as number[];
}

/**
 * The orb canvas's backing store and the device pixels the browser paints it
 * into, which must match for the character to render without resampling. The
 * painted size comes from the browser's own device-pixel content box: under
 * Playwright's viewport emulation `devicePixelRatio` reports the emulated
 * ratio while the window still rasterises at the display's.
 */
async function canvasFit(): Promise<Readonly<{ backing: string; painted: string; ratio: number }>> {
  const value = await shadow.read(
    "orb-canvas",
    "new Promise((resolve) => { const observer = new ResizeObserver(([entry]) => { observer.disconnect(); const box = entry.devicePixelContentBoxSize[0]; resolve(JSON.stringify({ backing: `${this.width}x${this.height}`, painted: `${box.inlineSize}x${box.blockSize}`, ratio: window.devicePixelRatio })); }); observer.observe(this, { box: 'device-pixel-content-box' }); })",
  );
  return JSON.parse(value ?? "{}") as { backing: string; painted: string; ratio: number };
}

/** Sets the preview tab's browser zoom, which changes its device pixel ratio. */
async function zoomPreview(factor: number): Promise<void> {
  await browser.worker.evaluate(async (zoom: number) => {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab?.id === undefined) {
      throw new Error("No active preview tab to zoom.");
    }
    await chrome.tabs.setZoom(tab.id, zoom);
  }, factor);
}

describe("the session tab conversation", () => {
  it("answers the participant compatibility preflight with what this installation can prove", async () => {
    const result = await session.evaluate(
      async (extensionId: string) =>
        (await (
          window as unknown as { runPreflight: (id: string) => Promise<unknown> }
        ).runPreflight(extensionId)) as {
          type: string;
          account_linked: boolean;
          host_permission: boolean;
          installation_proof: string;
          protocol_version: string;
        },
      browser.extensionId,
    );
    expect(result.type).toBe("utrace_preflight_result");
    expect(result.account_linked).toBe(true);
    // The control plane checks the extension protocol, not the session-tab message version.
    expect(result.protocol_version).toBe("1.0");
    // The end-to-end build declares both host permissions as required, because
    // a test profile cannot answer a permission prompt.
    expect(result.host_permission).toBe(true);
    // The proof is an opaque base64url document the page cannot read or forge.
    expect(result.installation_proof).toMatch(/^[A-Za-z0-9_-]{32,}$/u);
    const proof: unknown = JSON.parse(
      Buffer.from(result.installation_proof, "base64url").toString("utf8"),
    );
    expect(proof).toMatchObject({ algorithm: "ES256" });
  });

  it("accepts the session tab's offer and binds the focused preview tab", async () => {
    const answer = await session.evaluate(
      async ([extensionId, offer]: readonly [string, Readonly<Record<string, unknown>>]) =>
        (await (
          window as unknown as {
            sendBindingOffer: (id: string, payload: unknown) => Promise<unknown>;
          }
        ).sendBindingOffer(extensionId, offer)) as { type: string; accepted: boolean },
      [browser.extensionId, bindingOffer()] as const,
    );
    expect(answer.type).toBe("utrace_binding_offer_ack");
    expect(answer.accepted).toBe(true);

    const connected = await control.waitFor("recovery_request", () => true);
    expect(connected.payload.installation_id).toBe(INSTALLATION_ID);
    expect(control.subprotocols.some((value) => value.startsWith("utrace.pop."))).toBe(true);

    // Focusing the preview tab runs the page-proof probe; only a matching proof
    // binds, and only then is anything injected.
    await preview.bringToFront();
    const submitted = await control.waitFor("binding_offer", () => true);
    expect(submitted.payload.request).toStrictEqual({ kind: "offer", offer: bindingOffer() });
    expect(readPageProofClaims(submitted.payload.page_proof).session_id).toBe(SESSION_ID);
    expect(submitted.payload.tab_locator.frame_id).toBe(0);

    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "binding_state",
      payload: {
        binding_revision: 4,
        session_id: SESSION_ID,
        status: "bound",
        client_id: CLIENT_ID,
        extension_binding_id: BINDING_ID,
        document_attachment_id: ATTACHMENT_ID,
        preview_runtime_allocation_id: ALLOCATION_ID,
      },
    });

    shadow = await ShadowInspector.create(preview);
    await waitUntil("the agent surface mounts", () => shadow.exists("orb"));
  }, 60_000);

  it("renders the packaged Rive character on the orb", async () => {
    control.send({
      schema_version: SCHEMA_VERSION,
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
    );
    expect(await shadow.read("orb-canvas", "this.dataset.utraceRevision")).toBe("41");

    // The packaged canvas renderer must actually paint the character.
    const painted = await shadow.read(
      "orb-canvas",
      "(() => { const context = this.getContext('2d'); if (context === null) { return 'no-2d-context'; } const { data } = context.getImageData(0, 0, this.width, this.height); let opaque = 0; for (let index = 3; index < data.length; index += 4) { if (data[index] > 0) { opaque += 1; } } return String(opaque); })()",
    );
    expect(painted).not.toBe("no-2d-context");
    expect(Number(painted)).toBeGreaterThan(200);
  }, 60_000);

  it("draws the character at twice the old orb size on a whole-pixel backing store", async () => {
    const orb = await shadow.box("orb");
    expect(orb.width).toBe(DEFAULT_ORB_SETTINGS.diameter);
    expect(orb.width).toBeGreaterThanOrEqual(112);
    const [left, top] = await rect("orb");
    expect(Number.isInteger(left)).toBe(true);
    expect(Number.isInteger(top)).toBe(true);

    const fit = await canvasFit();
    expect(fit.backing).toBe(fit.painted);
  });

  it("re-sizes the canvas backing store when zoom changes the device pixel ratio", async () => {
    const unzoomed = (await canvasFit()).backing;
    const doubled = unzoomed
      .split("x")
      .map((side) => String(Number(side) * 2))
      .join("x");

    await zoomPreview(2);
    await waitUntil("the zoomed canvas matches its painted device pixels", async () => {
      const fit = await canvasFit();
      return fit.ratio === 2 && fit.backing === fit.painted;
    });
    expect((await canvasFit()).backing).toBe(doubled);

    await zoomPreview(1);
    await waitUntil("the canvas returns to one backing pixel per CSS pixel", async () => {
      const fit = await canvasFit();
      return fit.ratio === 1 && fit.backing === unzoomed;
    });
  }, 60_000);

  it("ignores a character revision older than the applied one", async () => {
    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "character_state",
      payload: {
        character_revision: 12,
        session_id: SESSION_ID,
        state: "concern",
        gaze_x: 0,
        gaze_y: 0,
        speech_energy: 0,
        motion_scale: 1,
      },
    });
    await new Promise((done) => setTimeout(done, 300));
    expect(await shadow.read("orb-canvas", "this.dataset.utraceState")).toBe("speaking");
  });
});

describe("controls", () => {
  it("expands four pills on hover and collapses them again", async () => {
    expect(await shadow.read("orb-root", "this.dataset.expanded")).toBe("false");
    await expandControls();

    for (const pill of ["pill-return", "pill-mute", "pill-pause", "pill-end"]) {
      expect(await shadow.exists(pill)).toBe(true);
      const box = await shadow.box(pill);
      expect(box.width).toBeGreaterThan(0);
    }

    // The four pills surround the orb: above, left, right, below.
    const orbBox = await shadow.box("orb");
    const above = await shadow.box("pill-return");
    const below = await shadow.box("pill-end");
    const left = await shadow.box("pill-mute");
    const right = await shadow.box("pill-pause");
    expect(above.y).toBeLessThan(orbBox.y);
    expect(below.y).toBeGreaterThan(orbBox.y + orbBox.height);
    expect(left.x).toBeLessThan(orbBox.x);
    expect(right.x).toBeGreaterThan(orbBox.x + orbBox.width);

    // Each pill is a comfortable target named by its label, resting on whole
    // pixels so its icon and text are not resampled.
    const names: Readonly<Record<string, string>> = {
      "pill-return": "Return to session",
      "pill-mute": "Mute",
      "pill-pause": "Pause",
      "pill-end": "End interview",
    };
    for (const [pill, name] of Object.entries(names)) {
      expect(await shadow.accessibleName(pill)).toBe(name);
      const [x, y, , height] = await rect(pill);
      expect(height).toBe(ORB_LAYOUT.pillHeight);
      expect(height).toBeGreaterThanOrEqual(32);
      expect(Number.isInteger(x)).toBe(true);
      expect(Number.isInteger(y)).toBe(true);
    }
    expect(await shadow.read("pill-mute", "this.dataset.state")).toBe("unmuted");
    expect(await shadow.read("pill-pause", "this.dataset.state")).toBe("running");

    // Resting in the gap between the orb and a pill, longer than the collapse
    // grace, keeps the controls open rather than flickering them shut.
    const [orbLeft = 0, orbTop = 0, , orbHeight = 0] = await rect("orb");
    await preview.mouse.move(orbLeft - ORB_LAYOUT.pillGap / 2, orbTop + orbHeight / 2);
    await new Promise((done) => setTimeout(done, 600));
    expect(await shadow.read("orb-root", "this.dataset.expanded")).toBe("true");

    await preview.mouse.move(10, 10);
    await waitUntil(
      "the controls collapse",
      async () => (await shadow.read("orb-root", "this.dataset.expanded")) === "false",
    );
  }, 60_000);

  it("opens the controls for the keyboard and confirms End in place", async () => {
    // The surface is the last thing in the document, so moving focus backwards
    // from the page lands on End interview.
    await preview.keyboard.press("Shift+Tab");
    await waitUntil(
      "keyboard focus opens the controls",
      async () => (await shadow.read("orb-root", "this.dataset.expanded")) === "true",
    );
    expect(await shadow.read("pill-end", "this.matches(':focus-visible')")).toBe("true");

    await preview.keyboard.press("Enter");
    await waitUntil(
      "End asks for confirmation",
      async () => (await shadow.read("end-confirmation", "this.dataset.open")) === "true",
    );
    expect(await shadow.read("orb-root", "this.dataset.expanded")).toBe("false");
    expect(await shadow.read("end-cancel", "this.matches(':focus')")).toBe("true");
    // The card sits clear of the orb, below it or above it, inside the viewport.
    const [cardLeft = 0, cardTop = 0, cardWidth = 0, cardHeight = 0] = await rect("end-confirmation");
    const [, orbTop = 0, , orbHeight = 0] = await rect("orb");
    expect(cardTop >= orbTop + orbHeight || cardTop + cardHeight <= orbTop).toBe(true);
    const viewport = preview.viewportSize();
    expect(cardLeft).toBeGreaterThanOrEqual(0);
    expect(cardTop).toBeGreaterThanOrEqual(0);
    expect(cardLeft + cardWidth).toBeLessThanOrEqual(viewport?.width ?? 0);
    expect(cardTop + cardHeight).toBeLessThanOrEqual(viewport?.height ?? 0);

    await preview.keyboard.press("Escape");
    await waitUntil(
      "Escape keeps the interview going",
      async () => (await shadow.read("end-confirmation", "this.dataset.open")) === "false",
    );
    expect(await shadow.read("pill-end", "this.matches(':focus-visible')")).toBe("true");
    expect(await shadow.read("orb-root", "this.dataset.expanded")).toBe("true");
    expect(control.received.some((frame) => frame.kind === "control_action")).toBe(false);

    await shadow.read("pill-end", "this.blur()");
    await waitUntil(
      "the controls close when focus leaves",
      async () => (await shadow.read("orb-root", "this.dataset.expanded")) === "false",
    );
  }, 60_000);
});

describe("visual commands", () => {
  it("draws a point-and-circle scene from SDK target geometry", async () => {
    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "readiness",
      payload: {
        readiness_revision: 9,
        session_id: SESSION_ID,
        extension_readiness: "ready",
        combined_readiness: "ready",
        blockers: [],
      },
    });

    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "visual_command",
      payload: {
        command_id: COMMAND_ID,
        client_id: CLIENT_ID,
        session_id: SESSION_ID,
        extension_binding_id: BINDING_ID,
        document_attachment_id: ATTACHMENT_ID,
        expected_lifecycle_revision: 22,
        deadline_at: new Date(Date.now() + 30_000).toISOString(),
        command: {
          type: "scene",
          scene: {
            scene_id: SCENE_ID,
            steps: [
              { step: "move", target_id: "tgt_filter_panel", target_revision: 3 },
              {
                step: "point",
                target_id: "tgt_save_view_button",
                target_revision: 7,
                dwell_ms: 100,
              },
              {
                step: "circle",
                target_id: "tgt_save_view_button",
                target_revision: 7,
                retain: true,
              },
            ],
          },
        },
      },
    });

    await waitUntil("a mark is drawn", async () => (await shadow.count("scene-mark")) > 0);
    expect(await shadow.read("agent-cursor", "this.dataset.visible")).toBe("true");

    const result = await control.waitFor(
      "command_result",
      (frame) => frame.payload.command_id === COMMAND_ID,
    );
    expect(result.payload.outcome).toBe("applied");
    expect(result.payload.extension_binding_id).toBe(BINDING_ID);
    expect(result.payload.observed_targets).toStrictEqual([
      { target_id: "tgt_filter_panel", target_revision: 3 },
      { target_id: "tgt_save_view_button", target_revision: 7 },
    ]);

    const queries = await preview.evaluate(
      () =>
        (window as unknown as { __utraceSdkStub: { targetQueries: number } }).__utraceSdkStub
          .targetQueries,
    );
    expect(queries).toBeGreaterThan(1);
  }, 60_000);

  it("refuses a stale target revision without touching the page", async () => {
    const staleCommandId = "0199a0c0-0000-7aaa-8000-0000000cd00a";
    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "visual_command",
      payload: {
        command_id: staleCommandId,
        client_id: CLIENT_ID,
        session_id: SESSION_ID,
        extension_binding_id: BINDING_ID,
        document_attachment_id: ATTACHMENT_ID,
        expected_lifecycle_revision: 22,
        deadline_at: new Date(Date.now() + 30_000).toISOString(),
        command: {
          type: "scene",
          scene: {
            scene_id: "0199a0c0-0000-7aaa-8000-000000005ce9",
            steps: [
              {
                step: "circle",
                target_id: "tgt_save_view_button",
                target_revision: 99,
                retain: true,
              },
            ],
          },
        },
      },
    });

    const result = await control.waitFor(
      "command_result",
      (frame) => frame.payload.command_id === staleCommandId,
    );
    expect(result.payload.outcome).toBe("stale_target");
    expect(result.payload.reason).toBe("target_revision_changed");
  }, 60_000);
});

describe("dragging the orb", () => {
  it("moves the orb, collapses the controls, and stores the new position", async () => {
    const before = await shadow.centre("orb");
    await expandControls();

    await preview.mouse.down();
    await preview.mouse.move(before.x - 200, before.y - 160, { steps: 12 });
    expect(await shadow.read("orb-root", "this.dataset.expanded")).toBe("false");
    await preview.mouse.up();

    const after = await shadow.centre("orb");
    expect(Math.round(after.x)).toBeLessThan(Math.round(before.x) - 100);
    expect(Math.round(after.y)).toBeLessThan(Math.round(before.y) - 100);

    const stored = await browser.worker.evaluate(async (key: string) => {
      const value = await chrome.storage.local.get(key);
      return JSON.stringify(value[key]);
    }, ORB_KEY);
    expect(stored).toContain("anchorXRatio");

    // Release brings the controls back while the pointer stays on the orb.
    await waitUntil(
      "the controls reopen on release",
      async () => (await shadow.read("orb-root", "this.dataset.expanded")) === "true",
    );
    await preview.mouse.move(10, 10);
  }, 60_000);
});

describe("pause", () => {
  it("clears the scene, notifies the shared runtime, and keeps Return to session usable", async () => {
    expect(await shadow.count("scene-mark")).toBeGreaterThan(0);

    await expandControls();
    const pause = await shadow.centre("pill-pause");
    await preview.mouse.move(pause.x, pause.y);
    await preview.mouse.click(pause.x, pause.y);

    const action = await control.waitFor("control_action", (frame) => frame.payload.action === "pause");
    expect(action.payload.session_id).toBe(SESSION_ID);
    expect(action.payload.locally_confirmed).toBe(false);

    control.send({
      schema_version: SCHEMA_VERSION,
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

    await waitUntil("the scene clears", async () => (await shadow.count("scene-mark")) === 0);
    await waitUntil("the shared runtime is told about the pause", async () => {
      const notices = await preview.evaluate(
        () =>
          (window as unknown as { __utraceSdkStub: { pauseNotices: { pause_state: string }[] } })
            .__utraceSdkStub.pauseNotices,
      );
      return notices.some((notice) => notice.pause_state === "paused");
    });

    expect(await shadow.read("pill-pause", "this.textContent")).toBe("Resume");
    expect(await shadow.read("pill-pause", "this.dataset.state")).toBe("paused");
    expect(await shadow.read("pill-return", "this.disabled")).toBe("false");
    await preview.mouse.move(10, 10);
  }, 60_000);

  it("rejects a visual command that arrives while the session is paused", async () => {
    const pausedCommandId = "0199a0c0-0000-7aaa-8000-0000000cd00b";
    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "visual_command",
      payload: {
        command_id: pausedCommandId,
        client_id: CLIENT_ID,
        session_id: SESSION_ID,
        extension_binding_id: BINDING_ID,
        document_attachment_id: ATTACHMENT_ID,
        expected_lifecycle_revision: 22,
        deadline_at: new Date(Date.now() + 30_000).toISOString(),
        command: { type: "clear_scene" },
      },
    });

    const result = await control.waitFor(
      "command_result",
      (frame) => frame.payload.command_id === pausedCommandId,
    );
    expect(result.payload.outcome).toBe("rejected");
    expect(result.payload.reason).toBe("session_paused");
  }, 60_000);
});

describe("control-plane pushes", () => {
  it("names its session again when the control connection reconnects", async () => {
    control.drop();

    // The control plane re-projects, and pushes to, the session named here.
    const resumed = await control.waitFor(
      "recovery_request",
      (frame) => frame.payload.session_id === SESSION_ID,
    );
    expect(resumed.payload.installation_id).toBe(INSTALLATION_ID);
    expect(resumed.payload.resume_from_sequence).toBeGreaterThan(0);
  }, 60_000);

  it("detaches the surface when a revocation from another surface is pushed", async () => {
    expect(await shadow.exists("orb")).toBe(true);

    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "binding_state",
      payload: {
        binding_revision: 5,
        session_id: SESSION_ID,
        status: "revoked",
        reason: "end_confirm",
      },
    });

    await waitUntil("the agent surface detaches", async () => !(await shadow.exists("orb")));
    // Authority is gone, so a command that still arrives is refused unrouted.
    const lateCommandId = "0199a0c0-0000-7aaa-8000-0000000cd00c";
    control.send({
      schema_version: SCHEMA_VERSION,
      kind: "visual_command",
      payload: {
        command_id: lateCommandId,
        client_id: CLIENT_ID,
        session_id: SESSION_ID,
        extension_binding_id: BINDING_ID,
        document_attachment_id: ATTACHMENT_ID,
        expected_lifecycle_revision: 22,
        deadline_at: new Date(Date.now() + 30_000).toISOString(),
        command: { type: "clear_scene" },
      },
    });
    const result = await control.waitFor(
      "command_result",
      (frame) => frame.payload.command_id === lateCommandId,
    );
    expect(result.payload.outcome).toBe("rejected");
    expect(result.payload.reason).toBe("no_active_binding");
  }, 60_000);
});
