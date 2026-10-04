/**
 * The real-browser harness.
 *
 * It stands up the two servers the extension talks to — a test control plane
 * implementing the documented `WS /v1/extension/control` contract, and a static
 * server for the session-tab and preview fixtures — then launches the installed
 * Google Chrome with the unpacked build. The control plane and the browser SDK
 * are built by other workstreams; these are test doubles of their contracts.
 */
import { createReadStream, existsSync, readdirSync } from "node:fs";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { homedir, tmpdir } from "node:os";
import { extname, join, normalize, resolve } from "node:path";
import { WebSocketServer, type WebSocket } from "ws";
import { chromium, type BrowserContext, type Page, type Worker } from "playwright-core";

import { SCHEMA_VERSION } from "../../src/protocol/common";
import {
  parseInboundFrame,
  parseOutboundFrame,
  type InboundFrame,
  type OutboundFrame,
} from "../../src/protocol/control";

export const CONTROL_PORT = 8411;
export const STATIC_PORT = 8410;

/** The control plane's close code for an installation it does not know or has revoked. */
const INSTALLATION_NOT_LINKED_CLOSE_CODE = 4003;

/**
 * Google Chrome 153 stable no longer honours `--load-extension`, so an
 * unpacked build can only be driven by a Chrome for Testing build. The lookup
 * uses the ones the Playwright and Puppeteer caches already hold and fails
 * loudly rather than silently testing something else.
 */
const CHROME_FOR_TESTING_CACHES = [
  {
    root: resolve(homedir(), "Library/Caches/ms-playwright"),
    prefix: "chromium-",
    relative: "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  },
  {
    root: resolve(homedir(), ".cache/puppeteer/chrome"),
    prefix: "mac_arm-",
    relative: "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  },
] as const;

export function resolveChromeExecutable(): string {
  const found: string[] = [];
  for (const cache of CHROME_FOR_TESTING_CACHES) {
    const entries = readdirSync(cache.root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name.startsWith(cache.prefix))
      .map((entry) => entry.name)
      .sort()
      .reverse();
    for (const entry of entries) {
      const candidate = join(cache.root, entry, cache.relative);
      if (existsSync(candidate)) {
        found.push(candidate);
      }
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

const PAGES_DIR = resolve(import.meta.dirname, "pages");
const EXTENSION_DIR = resolve(import.meta.dirname, "../../dist-e2e");

const CONTENT_TYPES: Readonly<Record<string, string>> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

export type ControlPlane = Readonly<{
  received: OutboundFrame[];
  subprotocols: string[];
  send: (frame: Omit<InboundFrame, "sequence" | "message_id" | "sent_at">) => void;
  waitFor: <Kind extends OutboundFrame["kind"]>(
    kind: Kind,
    predicate: (frame: Extract<OutboundFrame, { kind: Kind }>) => boolean,
  ) => Promise<Extract<OutboundFrame, { kind: Kind }>>;
  /** Refuses every later connection the way the control plane refuses an unlinked installation. */
  refuseInstallation: (reason: string) => void;
  /** Drops the current connection the way a network fault or a restart would. */
  drop: () => void;
  close: () => Promise<void>;
}>;

/** A test control plane speaking the documented extension control protocol. */
export async function startControlPlane(): Promise<ControlPlane> {
  const received: OutboundFrame[] = [];
  const subprotocols: string[] = [];
  const waiters = new Set<(frame: OutboundFrame) => void>();
  let socket: WebSocket | null = null;
  let sequence = 0;
  let refusal: string | null = null;

  const http = createServer((request: IncomingMessage, response: ServerResponse) => {
    if (request.method === "POST" && request.url === "/v1/extension/installations") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          schema_version: SCHEMA_VERSION,
          kind: "installation_registration_response",
          installation_id: INSTALLATION_ID,
          account_link_state: "linked",
          negotiated_protocol_version: "1.0",
          control_socket_path: "/v1/extension/control",
          registered_at: "2026-10-03T16:58:00Z",
        }),
      );
      return;
    }
    response.writeHead(404).end();
  });

  const sockets = new WebSocketServer({ server: http, path: "/v1/extension/control" });
  sockets.on("connection", (connection: WebSocket, request: IncomingMessage) => {
    if (refusal !== null) {
      connection.close(INSTALLATION_NOT_LINKED_CLOSE_CODE, refusal);
      return;
    }
    socket = connection;
    subprotocols.push(...(request.headers["sec-websocket-protocol"] ?? "").split(", "));
    connection.on("message", (data: Buffer) => {
      const frame = parseOutboundFrame(JSON.parse(data.toString("utf8")));
      received.push(frame);
      for (const waiter of [...waiters]) {
        waiter(frame);
      }
    });
  });

  await listen(http, CONTROL_PORT);

  return {
    received,
    subprotocols,
    send: (frame): void => {
      if (socket === null) {
        throw new Error("No extension is connected to the test control plane.");
      }
      sequence += 1;
      const complete = parseInboundFrame({
        ...frame,
        message_id: uuid(sequence),
        sent_at: new Date().toISOString(),
        ...(frame.kind === "heartbeat" ? {} : { sequence }),
      });
      socket.send(JSON.stringify(complete));
    },
    waitFor: <Kind extends OutboundFrame["kind"]>(
      kind: Kind,
      predicate: (frame: Extract<OutboundFrame, { kind: Kind }>) => boolean,
    ): Promise<Extract<OutboundFrame, { kind: Kind }>> => {
      const matches = (frame: OutboundFrame): Extract<OutboundFrame, { kind: Kind }> | null =>
        frame.kind === kind &&
        predicate(frame as Extract<OutboundFrame, { kind: Kind }>)
          ? (frame as Extract<OutboundFrame, { kind: Kind }>)
          : null;

      for (const frame of received) {
        const match = matches(frame);
        if (match !== null) {
          return Promise.resolve(match);
        }
      }
      return new Promise((resolveFrame, rejectFrame) => {
        const timer = setTimeout(() => {
          waiters.delete(waiter);
          rejectFrame(
            new Error(
              `No ${kind} frame matched within 15s; received ${received
                .map((frame) => frame.kind)
                .join(", ")}.`,
            ),
          );
        }, 15_000);
        const waiter = (frame: OutboundFrame): void => {
          const match = matches(frame);
          if (match === null) {
            return;
          }
          clearTimeout(timer);
          waiters.delete(waiter);
          resolveFrame(match);
        };
        waiters.add(waiter);
      });
    },
    refuseInstallation: (reason: string): void => {
      refusal = reason;
    },
    drop: (): void => {
      if (socket === null) {
        throw new Error("No extension is connected to the test control plane.");
      }
      socket.terminate();
      socket = null;
    },
    close: async (): Promise<void> => {
      sockets.close();
      await new Promise<void>((done) => {
        http.close(() => {
          done();
        });
      });
    },
  };
}

export async function startStaticServer(): Promise<Server> {
  const server = createServer((request: IncomingMessage, response: ServerResponse) => {
    const requested = normalize(new URL(request.url ?? "/", "http://localhost").pathname).replace(
      /^(\.\.[/\\])+/u,
      "",
    );
    const file = join(PAGES_DIR, requested);
    if (!file.startsWith(PAGES_DIR)) {
      response.writeHead(403).end();
      return;
    }
    void stat(file)
      .then(() => {
        response.writeHead(200, {
          "content-type": CONTENT_TYPES[extname(file)] ?? "application/octet-stream",
        });
        createReadStream(file).pipe(response);
      })
      .catch(() => {
        response.writeHead(404).end();
      });
  });

  await listen(server, STATIC_PORT);
  return server;
}

export const INSTALLATION_ID = "0199a0c0-0000-7aaa-8000-000000001457";
export const SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e1";
export const CLIENT_ID = "0199a0c0-0000-7aaa-8000-00000000c101";
export const ALLOCATION_ID = "0199a0c0-0000-7aaa-8000-00000000a110";
export const BINDING_ID = "0199a0c0-0000-7aaa-8000-0000000b1d01";
export const ATTACHMENT_ID = "0199a0c0-0000-7aaa-8000-0000000a7701";

export type Browser = Readonly<{
  context: BrowserContext;
  worker: Worker;
  extensionId: string;
  close: () => Promise<void>;
}>;

/**
 * Launches Chrome on a fresh profile that holds the installation record the
 * account-link flow would write. The link flow itself opens an interactive
 * Chrome window, so it stays outside this automated run. The service worker
 * reads its installation once, when it starts, so seeding a running worker
 * races that read: the record is written in a first launch and read by a
 * second launch of the same profile.
 */
export async function launchLinkedChrome(installationId: string): Promise<Browser> {
  await readFile(join(EXTENSION_DIR, "manifest.json"), "utf8");
  const profile = await mkdtemp(join(tmpdir(), "utrace-extension-"));

  const seeding = await launchProfile(profile);
  await seeding.worker.evaluate(async (id: string) => {
    await chrome.storage.local.set({
      installation: {
        installation_id: id,
        account_link_state: "linked",
        negotiated_protocol_version: "1.0",
        registered_at: "2026-10-03T16:58:00Z",
      },
    });
  }, installationId);
  await seeding.context.close();

  const { context, worker } = await launchProfile(profile);
  return {
    context,
    worker,
    extensionId: new URL(worker.url()).host,
    close: async (): Promise<void> => {
      await context.close();
      await rm(profile, { recursive: true, force: true });
    },
  };
}

async function launchProfile(
  profile: string,
): Promise<Readonly<{ context: BrowserContext; worker: Worker }>> {
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: resolveChromeExecutable(),
    headless: false,
    args: [
      `--disable-extensions-except=${EXTENSION_DIR}`,
      `--load-extension=${EXTENSION_DIR}`,
      "--no-first-run",
      "--no-default-browser-check",
    ],
  });
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  return { context, worker };
}

export function bindingOffer(): Readonly<Record<string, unknown>> {
  return {
    schema_version: SCHEMA_VERSION,
    offer_token: "b2ZmZXJfdG9rZW5fZXhhbXBsZV92YWx1ZQ",
    session_id: SESSION_ID,
    client_id: CLIENT_ID,
    extension_installation_id: INSTALLATION_ID,
    preview_runtime_allocation_id: ALLOCATION_ID,
    protocol_version: "1.0",
    expires_at: "2126-09-20T17:06:04Z",
  };
}

export async function waitUntil(
  description: string,
  predicate: () => Promise<boolean>,
): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await predicate()) {
      return;
    }
    await new Promise((done) => setTimeout(done, 100));
  }
  throw new Error(`Timed out waiting until ${description}.`);
}

export async function openPage(context: BrowserContext, path: string): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`http://localhost:${STATIC_PORT}${path}`, { waitUntil: "load" });
  return page;
}

function listen(server: Server, port: number): Promise<void> {
  return new Promise((done, fail) => {
    server.once("error", fail);
    server.listen(port, "127.0.0.1", () => {
      server.removeListener("error", fail);
      done();
    });
  });
}

function uuid(counter: number): string {
  return `0199a0c0-0000-7aaa-8000-${counter.toString(16).padStart(12, "0")}`;
}
