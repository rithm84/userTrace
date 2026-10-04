/**
 * An installation the control plane no longer knows, driven in real Chrome.
 *
 * After a reset or a server-side revocation, the control plane refuses the
 * installation on the control socket with its own close code. From a freshly
 * started service worker only the preflight's connectivity check reaches it,
 * because no binding offer is minted until a preflight passes. That refusal
 * alone unlinks the browser; an unreachable control plane does not.
 */
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Page } from "playwright-core";

import type { StatusSnapshot } from "../../src/background/session";
import {
  INSTALLATION_ID,
  launchLinkedChrome,
  openPage,
  startControlPlane,
  startStaticServer,
  waitUntil,
  type Browser,
  type ControlPlane,
} from "./harness";

type PreflightAnswer = Readonly<{ account_linked: boolean; installation_proof: string | null }>;

let statics: Server;
let browser: Browser;
let session: Page;
let popup: Page;
let control: ControlPlane | null = null;

beforeAll(async () => {
  statics = await startStaticServer();
  browser = await launchLinkedChrome(INSTALLATION_ID);
  session = await openPage(browser.context, "/session.html");
  popup = await browser.context.newPage();
  await popup.goto(`chrome-extension://${browser.extensionId}/popup.html`);
}, 120_000);

afterAll(async () => {
  await browser.close();
  await control?.close();
  await new Promise<void>((done) => {
    statics.close(() => {
      done();
    });
  });
});

function preflight(): Promise<PreflightAnswer> {
  return session.evaluate(
    async (extensionId: string) =>
      (await (
        window as unknown as { runPreflight: (id: string) => Promise<unknown> }
      ).runPreflight(extensionId)) as PreflightAnswer,
    browser.extensionId,
  );
}

async function workerStatus(): Promise<StatusSnapshot> {
  const response = await popup.evaluate(() =>
    chrome.runtime.sendMessage<{ kind: "status" }, { status: StatusSnapshot }>({ kind: "status" }),
  );
  return response.status;
}

function testId(id: string): string {
  return `[data-utrace-testid="${id}"]`;
}

describe("an installation the control plane refuses", () => {
  it("stays linked when the control plane cannot be reached", async () => {
    // Nothing listens on the control port yet, so the preflight's connection
    // fails the way a network fault does.
    await preflight();
    await waitUntil(
      "the failed connection settles",
      async () => (await workerStatus()).connectionReason === "socket_closed_1006",
    );
    expect((await workerStatus()).installation?.installation_id).toBe(INSTALLATION_ID);
  }, 60_000);

  it("drops the installation and offers the account link again", async () => {
    control = await startControlPlane();
    control.refuseInstallation("installation_unknown");
    await preflight();

    await waitUntil("the installation record is dropped", () =>
      browser.worker.evaluate(
        async () => (await chrome.storage.local.get("installation"))["installation"] === undefined,
      ),
    );

    await popup.reload();
    expect(await popup.locator(testId("status-installation")).textContent()).toBe("Not linked");
    expect(await popup.locator(testId("status-account-link")).textContent()).toBe("none");
    expect(await popup.locator(testId("status-connection")).textContent()).toBe(
      "idle (installation_unknown)",
    );
    expect(await popup.locator(testId("link-account")).isEnabled()).toBe(true);

    const answer = await preflight();
    expect(answer.account_linked).toBe(false);
    expect(answer.installation_proof).toBeNull();
  }, 60_000);
});
