/**
 * The real Chrome the journey drives.
 *
 * Chrome stable from 153 no longer loads an unpacked extension from the command
 * line, so the journey uses a Chrome for Testing build from the Playwright or
 * Puppeteer cache and fails loudly rather than quietly testing something else.
 *
 * Two launch flags exist because the journey serves a session origin rather
 * than `localhost`: a resolver rule points `*.preview.localtest` at the
 * loopback address, and the self-signed certificate the TLS terminator
 * generated for this run is accepted. Neither flag changes what the product
 * does; they make a subdomain origin reachable and secure, which is what a real
 * preview origin already is.
 *
 * Chrome must be 137 or newer: WebCrypto gained Ed25519 there, and the browser
 * SDK verifies every activation envelope with it. An older build fails the
 * activation gate, which is the correct behaviour and an unhelpful test.
 */

import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { chromium, type BrowserContext, type Page, type Worker } from "playwright-core";

/** WebCrypto Ed25519 shipped in Chrome 137. */
export const MINIMUM_CHROME_MAJOR = 137;

const CHROME_FOR_TESTING_CACHES = [
  {
    root: resolve(homedir(), "Library/Caches/ms-playwright"),
    prefix: "chromium-",
    relative:
      "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  },
  {
    root: resolve(homedir(), ".cache/puppeteer/chrome"),
    prefix: "mac_arm-",
    relative:
      "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  },
] as const;

export function resolveChromeExecutable(): string {
  const found: string[] = [];
  for (const cache of CHROME_FOR_TESTING_CACHES) {
    if (!existsSync(cache.root)) continue;
    const entries = readdirSync(cache.root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name.startsWith(cache.prefix))
      .map((entry) => entry.name)
      .sort()
      .reverse();
    for (const entry of entries) {
      const candidate = join(cache.root, entry, cache.relative);
      if (existsSync(candidate)) found.push(candidate);
    }
  }
  const executable = found[0];
  if (executable === undefined) {
    throw new Error(
      "No Chrome for Testing build was found. Run `npx playwright install chromium` first; " +
        "installed Google Chrome stable no longer loads unpacked extensions from the command line.",
    );
  }
  return executable;
}

export interface JourneyBrowser {
  readonly context: BrowserContext;
  readonly worker: Worker;
  readonly extensionId: string;
  readonly close: () => Promise<void>;
}

export async function launchChrome(params: {
  readonly extensionDirectory: string;
  readonly previewApex: string;
}): Promise<JourneyBrowser> {
  if (!existsSync(join(params.extensionDirectory, "manifest.json"))) {
    throw new Error(
      `${params.extensionDirectory} holds no packaged extension; build it before the journey.`,
    );
  }
  const context = await chromium.launchPersistentContext("", {
    executablePath: resolveChromeExecutable(),
    headless: false,
    ignoreHTTPSErrors: true,
    args: [
      `--disable-extensions-except=${params.extensionDirectory}`,
      `--load-extension=${params.extensionDirectory}`,
      `--host-resolver-rules=MAP *.${params.previewApex} 127.0.0.1`,
      "--ignore-certificate-errors",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-features=DialMediaRouteProvider",
    ],
  });

  const version = await readMajorVersion(context);
  if (version < MINIMUM_CHROME_MAJOR) {
    await context.close();
    throw new Error(
      `Chrome ${String(version)} cannot verify Ed25519 in WebCrypto, so the browser SDK's ` +
        `activation gate can never pass. The journey needs Chrome ${String(MINIMUM_CHROME_MAJOR)} or newer.`,
    );
  }

  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  return {
    context,
    worker,
    extensionId: new URL(worker.url()).host,
    close: (): Promise<void> => context.close(),
  };
}

async function readMajorVersion(context: BrowserContext): Promise<number> {
  const page: Page = await context.newPage();
  const agent = await page.evaluate(() => navigator.userAgent);
  await page.close();
  const match = /Chrome\/(\d+)/u.exec(agent);
  if (match === null) {
    throw new Error(`the launched browser does not report a Chrome version: ${agent}`);
  }
  return Number.parseInt(match[1] ?? "0", 10);
}

/** Seeds the installation the one-time account link would have written. */
export async function seedInstallation(worker: Worker, installationId: string): Promise<void> {
  await worker.evaluate(async (id: string) => {
    await chrome.storage.local.set({
      installation: {
        installation_id: id,
        account_link_state: "linked",
        negotiated_protocol_version: "1.0",
        registered_at: new Date().toISOString().replace(/\.\d+Z$/u, "Z"),
      },
    });
  }, installationId);
}
