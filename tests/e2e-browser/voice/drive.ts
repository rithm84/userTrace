/**
 * Drives one voice call through the real uTrace session tab in Chrome.
 *
 * The orchestrating test (`apps/api/tests/interview/test_voice_browser_live.py`)
 * starts the real API, worker, and web app against the local stack, prepares the
 * session, and hands this driver a JSON config: the page to open, the participant's
 * Supabase session, the participant's recorded lines, and the steps to take. The
 * driver signs the browser in with the cookies `@supabase/ssr` itself writes, opens
 * the session tab, speaks through a synthetic microphone, and records what the
 * participant would actually hear and see.
 *
 * The microphone is a `MediaStreamTrackGenerator` that `getUserMedia` returns, so
 * speaking needs no user gesture. What the participant hears is the agent's track
 * as the session tab's own `<audio>` element plays it: its level counts only while
 * that element is playing, so audio the browser's autoplay policy blocked is
 * measured as the silence it is. Chrome keeps its real autoplay policy.
 *
 * Usage: `node voice/drive.ts <config.json>`. It writes the report the config
 * names and exits non-zero only when it cannot run the steps at all.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, type BrowserContext, type Page } from "playwright-core";

import { resolveChromeExecutable } from "../support/chrome.ts";

// The web app's own `@supabase/ssr`, loaded from its CommonJS build because the
// ESM build uses bundler-style extensionless imports that Node does not resolve.
type SupabaseSsr = typeof import("../../../apps/web/node_modules/@supabase/ssr/dist/main/index.js");
type CookieMethods = import("../../../apps/web/node_modules/@supabase/ssr/dist/main/types.js").CookieMethodsServer;
const ssr = createRequire(import.meta.url)(
  "../../../apps/web/node_modules/@supabase/ssr/dist/main/index.js",
) as SupabaseSsr;

type Step =
  | { readonly do: "open" }
  | { readonly do: "gesture" }
  | { readonly do: "reload" }
  | { readonly do: "say"; readonly clip: string }
  | { readonly do: "sleep"; readonly ms: number }
  | { readonly do: "mark"; readonly label: string }
  | { readonly do: "click"; readonly testId: string }
  | { readonly do: "waitAgentSpeech"; readonly label: string; readonly timeoutMs: number }
  | { readonly do: "waitAgentQuiet"; readonly quietMs: number; readonly timeoutMs: number };

interface Config {
  readonly webOrigin: string;
  readonly sessionPath: string;
  readonly supabaseUrl: string;
  readonly publishableKey: string;
  readonly accessToken: string;
  readonly refreshToken: string;
  readonly clips: Readonly<Record<string, string>>;
  readonly steps: readonly Step[];
  readonly reportPath: string;
  readonly headless: boolean;
}

interface Event {
  readonly at: number;
  readonly kind: string;
  readonly detail: string;
}

/** Level, on a 0 to 1 scale, above which the agent's played audio is speech. */
const SPEECH_RMS = 0.01;
/** Speech must last this long to count, so a click is not speech. */
const SPEECH_ONSET_MS = 200;
const POLL_MS = 50;

const config = JSON.parse(readFileSync(process.argv[2] ?? "", "utf8")) as Config;
const events: Event[] = [];
const started = Date.now();
const log = (kind: string, detail: string): void => {
  events.push({ at: (Date.now() - started) / 1000, kind, detail });
};

/** The cookies the app's own `@supabase/ssr` client writes for this session. */
async function authCookies(): Promise<{ name: string; value: string }[]> {
  const written = new Map<string, string>();
  const cookies: CookieMethods = {
    getAll: () => [...written].map(([name, value]) => ({ name, value })),
    setAll: (toSet) => {
      for (const { name, value } of toSet) written.set(name, value);
    },
  };
  const supabase = ssr.createServerClient(config.supabaseUrl, config.publishableKey, { cookies });
  const { error } = await supabase.auth.setSession({
    access_token: config.accessToken,
    refresh_token: config.refreshToken,
  });
  if (error !== null) throw new Error(`Supabase could not set the participant session: ${error.message}`);
  if (written.size === 0) throw new Error("Supabase wrote no auth cookies for the participant session");
  return [...written].map(([name, value]) => ({ name, value }));
}

/**
 * The page-side instrumentation: the synthetic microphone and the meter on what the
 * session tab plays. It runs before any page script, on every navigation.
 */
const INSTRUMENT = String.raw`
(() => {
  const RATE = 48000;
  const FRAME = 480;
  const state = { queue: [], levels: [], playback: [], micReady: false };
  window.__utrace = state;

  const generator = new MediaStreamTrackGenerator({ kind: "audio" });
  const writer = generator.writable.getWriter();
  let timestamp = 0;
  const silence = new Float32Array(FRAME);
  setInterval(() => {
    // Ten milliseconds of audio every ten milliseconds, the way a microphone delivers it.
    const chunk = state.queue.length > 0 ? state.queue.shift() : silence;
    const data = new AudioData({
      format: "f32-planar", sampleRate: RATE, numberOfFrames: FRAME,
      numberOfChannels: 1, timestamp, data: chunk,
    });
    timestamp += (FRAME / RATE) * 1e6;
    writer.write(data).catch(() => {});
  }, 10);

  window.__utraceSay = (base64) => {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const pcm = new Int16Array(bytes.buffer);
    for (let start = 0; start < pcm.length; start += FRAME) {
      const frame = new Float32Array(FRAME);
      for (let i = 0; i < FRAME && start + i < pcm.length; i += 1) frame[i] = pcm[start + i] / 32768;
      state.queue.push(frame);
    }
    return (pcm.length / RATE) * 1000;
  };
  window.__utraceSpeaking = () => state.queue.length > 0;
  let peak = 0;
  window.__utraceDiagnose = () => {
    const element = document.querySelector('[data-testid="session-audio"]');
    const track = element && element.srcObject ? element.srcObject.getAudioTracks()[0] : null;
    for (const level of state.levels) peak = Math.max(peak, level.rms);
    return JSON.stringify({
      mic: state.micReady,
      element: Boolean(element),
      paused: element ? element.paused : null,
      track: track ? track.readyState + (track.muted ? "/muted" : "") : null,
      peak: Math.round(peak * 1000) / 1000,
      errors: state.playback.filter((entry) => entry.error).length,
    });
  };

  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    if (constraints && constraints.audio) {
      state.micReady = true;
      return new MediaStream([generator.clone()]);
    }
    return original(constraints);
  };

  // What the session tab plays: the agent's track, counted only while the tab's
  // <audio> element is actually playing it, so audio the autoplay policy blocked
  // is the silence the participant hears.
  let metered = null;
  setInterval(() => {
    const element = document.querySelector('[data-testid="session-audio"]');
    if (!element || !element.srcObject) return;
    const track = element.srcObject.getAudioTracks()[0];
    if (!track || track.readyState !== "live" || metered === track) return;
    metered = track;
    let reader;
    try {
      reader = new MediaStreamTrackProcessor({ track }).readable.getReader();
    } catch (error) {
      state.playback.push({ at: performance.now(), error: String(error) });
      return;
    }
    (async () => {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        const samples = new Float32Array(value.numberOfFrames);
        value.copyTo(samples, { planeIndex: 0, format: "f32-planar" });
        value.close();
        let sum = 0;
        for (const sample of samples) sum += sample * sample;
        const playing = !element.paused && element.srcObject && element.srcObject.getAudioTracks()[0] === track;
        state.levels.push({ at: performance.now(), rms: playing ? Math.sqrt(sum / samples.length) : 0, paused: element.paused });
      }
    })();
  }, 50);
})();
`;

/** Tracks whether the agent is audibly speaking, from the page's level samples. */
class Hearing {
  speaking = false;
  private loudSince: number | null = null;
  private quietSince: number = Date.now();
  heardAt: number[] = [];

  async poll(page: Page): Promise<void> {
    const levels = (await page
      .evaluate("(() => { const l = window.__utrace ? window.__utrace.levels.splice(0) : []; return l; })()")
      .catch(() => [])) as { rms: number }[];
    const loud = levels.some((level) => level.rms >= SPEECH_RMS);
    const now = Date.now();
    if (loud) {
      this.quietSince = now;
      this.loudSince ??= now;
      if (!this.speaking && now - this.loudSince >= SPEECH_ONSET_MS) {
        this.speaking = true;
        this.heardAt.push(now);
        log("agent_audible", "the agent's audio is playing in the session tab");
      }
    } else if (levels.length > 0) {
      this.loudSince = null;
      if (this.speaking && now - this.quietSince >= 700) {
        this.speaking = false;
        log("agent_quiet", "the agent's audio stopped");
      }
    }
  }

  quietFor(): number {
    return this.speaking ? 0 : Date.now() - this.quietSince;
  }
}

async function watchText(page: Page, testId: string, seen: Map<string, string>): Promise<void> {
  const text = await page
    .getByTestId(testId)
    .first()
    .textContent({ timeout: 50 })
    .catch(() => null);
  const value = text === null ? "" : text.trim();
  if (seen.get(testId) !== value) {
    seen.set(testId, value);
    if (value !== "") log(testId, value);
  }
}

async function run(): Promise<void> {
  const context: BrowserContext = await chromium.launchPersistentContext("", {
    executablePath: resolveChromeExecutable(),
    headless: config.headless,
    args: ["--autoplay-policy=user-gesture-required", "--use-fake-ui-for-media-stream"],
  });
  const origin = new URL(config.webOrigin);
  await context.addCookies(
    (await authCookies()).map((cookie) => ({
      ...cookie,
      domain: origin.hostname,
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax" as const,
    })),
  );
  await context.addInitScript(INSTRUMENT);
  const page = context.pages()[0] ?? (await context.newPage());
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      log(`console_${message.type()}`, message.text().slice(0, 400));
    }
  });
  page.on("pageerror", (error) => {
    log("page_error", error.message.slice(0, 400));
  });
  page.on("requestfailed", (request) => {
    log("request_failed", `${request.method()} ${request.url().slice(0, 160)}: ${request.failure()?.errorText ?? ""}`);
  });

  const hearing = new Hearing();
  const texts = new Map<string, string>();
  const watched = ["session-status", "session-voice-failure", "session-notice", "session-connection", "session-realtime-status"];
  const run = { watching: true };
  let diagnosis = "";
  let lastDiagnosed = 0;
  const watcher = (async () => {
    while (run.watching) {
      if (Date.now() - lastDiagnosed > 1000) {
        lastDiagnosed = Date.now();
        const now = (await page.evaluate("window.__utraceDiagnose ? window.__utraceDiagnose() : ''").catch(() => "")) as string;
        if (now !== diagnosis) {
          diagnosis = now;
          log("playback", now);
        }
      }
      await hearing.poll(page);
      for (const testId of watched) await watchText(page, testId, texts);
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
  })();

  const until = async (condition: () => boolean, timeoutMs: number, what: string): Promise<boolean> => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (condition()) return true;
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
    log("timeout", what);
    return false;
  };

  for (const step of config.steps) {
    log("step", JSON.stringify(step));
    switch (step.do) {
      case "open":
        await page.goto(new URL(config.sessionPath, config.webOrigin).toString(), { waitUntil: "load" });
        break;
      case "reload":
        await page.reload({ waitUntil: "load" });
        break;
      case "gesture":
        await page.mouse.click(5, 5);
        break;
      case "click":
        await page.getByTestId(step.testId).click();
        break;
      case "sleep":
        await new Promise((resolve) => setTimeout(resolve, step.ms));
        break;
      case "mark":
        log("mark", step.label);
        break;
      case "say": {
        const path = config.clips[step.clip];
        if (path === undefined) throw new Error(`no clip named ${step.clip}`);
        const audio = readFileSync(path).toString("base64");
        const ms: unknown = await page.evaluate(`window.__utraceSay(${JSON.stringify(audio)})`);
        if (typeof ms !== "number") throw new Error(`the page could not queue clip ${step.clip}`);
        log("participant_speaking", step.clip);
        await new Promise((resolve) => setTimeout(resolve, ms + 100));
        log("participant_done", step.clip);
        break;
      }
      case "waitAgentSpeech": {
        const heard = hearing.heardAt.length;
        if (await until(() => hearing.heardAt.length > heard, step.timeoutMs, `no agent speech for ${step.label}`)) {
          log("heard", step.label);
        }
        break;
      }
      case "waitAgentQuiet":
        await until(() => hearing.quietFor() >= step.quietMs, step.timeoutMs, "the agent never went quiet");
        break;
    }
  }

  run.watching = false;
  await watcher;
  const transcript = await page
    .getByTestId("session-transcript-turns")
    .innerText({ timeout: 2000 })
    .catch(() => "");
  writeFileSync(config.reportPath, JSON.stringify({ events, transcript }, null, 2));
  await context.close();
}

await run();
