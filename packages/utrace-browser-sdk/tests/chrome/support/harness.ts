/**
 * Real-Chrome harness.
 *
 * Bundles the static fixture application against the SDK source, replacing the
 * packaged signing trust anchor with this run's generated public key so
 * signature verification is genuinely exercised, then starts the test preview
 * front door, the third-party origin, and the test ingestion WebSocket, and
 * launches the locally installed Google Chrome through playwright-core.
 *
 * Ports stay inside this workstream's 8200-8299 range.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium, type Browser, type Page } from "playwright-core";
import { startControlPlane, startThirdPartyOrigin, type ControlPlane, type EchoedRequest } from "./control-plane.js";
import { startIngest, type IngestServer } from "./ingest.js";
import { createSigningKey } from "./signing.js";

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = resolve(here, "../fixture");
const BUILD_DIR = join(FIXTURE_DIR, "build");
const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

export const APP_PORT = 8210;
export const INGEST_PORT = 8211;
export const THIRD_PARTY_PORT = 8212;

/** The telemetry credential lifetime a test gets unless it shortens it. */
export const CREDENTIAL_LIFETIME_MS = 10 * 60_000;

export interface Harness {
  readonly page: Page;
  readonly controlPlane: ControlPlane;
  readonly ingest: IngestServer;
  readonly thirdPartyRequests: readonly EchoedRequest[];
  readonly tamperNextAttestation: { value: boolean };
  readonly credentialLifetimeMs: { value: number };
  readonly reload: () => Promise<void>;
  readonly stop: () => Promise<void>;
}

export interface HarnessOptions {
  /** Lets a test force baseline chunking by shrinking the per-observation limit. */
  readonly maxObservationBytes: number;
  /** The fixture path to open. `/dashboard/conversions/framed` embeds a child frame. */
  readonly path: string;
}

/** Bundles both fixture entry points and returns the build directory. */
const bundleFixture = async (publicKeyB64Url: string, keyId: string): Promise<string> => {
  mkdirSync(BUILD_DIR, { recursive: true });
  const keysModule = join(BUILD_DIR, "test-signing-keys.ts");
  writeFileSync(
    keysModule,
    `// Generated per test run by tests/chrome/support/harness.ts.\n` +
      `export const PACKAGED_SIGNING_KEYS = Object.freeze([\n` +
      `  Object.freeze({ key_id: ${JSON.stringify(keyId)}, algorithm: "ed25519", public_key_b64url: ${JSON.stringify(publicKeyB64Url)} }),\n` +
      `]);\n` +
      `export const findPackagedKey = (id) => PACKAGED_SIGNING_KEYS.find((key) => key.key_id === id) ?? null;\n`,
  );
  await build({
    entryPoints: [join(FIXTURE_DIR, "app.ts"), join(FIXTURE_DIR, "frame.ts")],
    outdir: BUILD_DIR,
    bundle: true,
    format: "esm",
    target: "chrome116",
    sourcemap: "inline",
    logLevel: "silent",
    plugins: [
      {
        name: "utrace-test-trust-anchor",
        setup(builder): void {
          builder.onResolve({ filter: /signing-keys\.js$/ }, () => ({ path: keysModule }));
        },
      },
    ],
  });
  return BUILD_DIR;
};

export const startHarness = async (options: HarnessOptions): Promise<Harness> => {
  const signing = createSigningKey("utrace-browser-test");
  const bundleDir = await bundleFixture(signing.publicKeyB64Url, signing.keyId);
  const manifest = JSON.parse(readFileSync(join(FIXTURE_DIR, "utrace.annotations.json"), "utf8")) as Record<
    string,
    unknown
  >;
  const fullManifest = {
    manifest_version: "test-manifest-1",
    client_release_id: "shopalytics-main-test",
    ...manifest,
  };

  const ingest = await startIngest(INGEST_PORT);
  const thirdParty = await startThirdPartyOrigin(THIRD_PARTY_PORT);
  const tamperNextAttestation = { value: false };
  const credentialLifetimeMs = { value: CREDENTIAL_LIFETIME_MS };
  const controlPlane = await startControlPlane({
    port: APP_PORT,
    fixtureDir: FIXTURE_DIR,
    bundleDir,
    ingestUrl: ingest.url,
    thirdPartyOrigin: thirdParty.origin,
    signing,
    manifest: fullManifest,
    tamperNextAttestation,
    credentialLifetimeMs,
    maxObservationBytes: options.maxObservationBytes,
  });

  let browser: Browser;
  try {
    browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  } catch (cause) {
    await Promise.all([ingest.close(), thirdParty.close(), controlPlane.close()]);
    throw new Error(`Google Chrome could not be launched from ${CHROME_PATH}`, { cause });
  }
  const page = await browser.newPage();
  page.on("pageerror", (error) => {
    process.stderr.write(`[fixture page error] ${error.message}\n`);
  });
  await page.goto(`${controlPlane.origin}${options.path}`, { waitUntil: "load" });

  return {
    page,
    controlPlane,
    ingest,
    thirdPartyRequests: thirdParty.requests,
    tamperNextAttestation,
    credentialLifetimeMs,
    reload: async (): Promise<void> => {
      await page.reload({ waitUntil: "load" });
    },
    stop: async (): Promise<void> => {
      await browser.close();
      await Promise.all([ingest.close(), thirdParty.close(), controlPlane.close()]);
    },
  };
};

/** Waits for the fixture's activation to settle, and surfaces its failure verbatim. */
export const waitForReady = async (page: Page): Promise<void> => {
  await page.waitForFunction(() => window.__utraceTest !== undefined, undefined, { timeout: 15_000 });
  await page.evaluate(async () => {
    await window.__utraceTest?.ready;
  });
};

/**
 * Waits until the ingestion edge has been quiet for `quietMs`, so a test that
 * counts records afterwards is not racing an earlier interaction's delivery.
 */
export const settle = async (read: () => number, quietMs: number, timeoutMs: number): Promise<void> => {
  const deadline = Date.now() + timeoutMs;
  let last = read();
  let quietSince = Date.now();
  while (Date.now() - quietSince < quietMs) {
    if (Date.now() > deadline) throw new Error("ingestion never went quiet");
    await new Promise<void>((done) => setTimeout(done, 25));
    const current = read();
    if (current !== last) {
      last = current;
      quietSince = Date.now();
    }
  }
};

/**
 * Polls until `predicate` holds or the timeout elapses, then reports the last
 * value. `read` may be asynchronous, which is what reading state out of the
 * page requires.
 */
export const waitFor = async <T>(
  read: () => T | Promise<T>,
  predicate: (value: T) => boolean,
  timeoutMs: number,
  description: string,
): Promise<T> => {
  const deadline = Date.now() + timeoutMs;
  let value = await read();
  while (!predicate(value)) {
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for ${description}; last value ${JSON.stringify(value)}`);
    }
    await new Promise<void>((done) => setTimeout(done, 50));
    value = await read();
  }
  return value;
};
