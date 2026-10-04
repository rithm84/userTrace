/**
 * The three real applications the journey runs.
 *
 * Each is started as its own process with `next start` against a production
 * build, because that is what a participant meets: a dev server recompiles on
 * demand, which would make the activation gate's timing a property of the test
 * rather than of the product.
 *
 * Nothing here is a stand-in. `apps/web` is the uTrace session tab,
 * `apps/preview-front-door` is the door, and the Shopalytics checkout is the
 * client product in its preview profile with both SDKs installed.
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";

export interface RunningApp {
  readonly name: string;
  readonly close: () => Promise<void>;
}

export interface AppSpec {
  readonly name: string;
  readonly directory: string;
  /** Environment the build and the start share. */
  readonly environment: Readonly<Record<string, string>>;
  /** A URL that answers once the app is serving. */
  readonly readyUrl: string;
  /** `next start` port. */
  readonly port: number;
  /** The npm script that starts it. */
  readonly startScript: string;
}

const log = (name: string, line: string): void => {
  process.stdout.write(`[${name}] ${line}`);
};

/** Builds one application. A build failure fails the journey immediately. */
export function buildApp(spec: AppSpec): void {
  if (!existsSync(`${spec.directory}/node_modules`)) {
    throw new Error(
      `${spec.name} has no node_modules; run \`npm ci\` in ${spec.directory} before the journey.`,
    );
  }
  const result = spawnSync("npm", ["run", "build"], {
    cwd: spec.directory,
    env: { ...process.env, ...spec.environment },
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `${spec.name} did not build:\n${result.stdout}\n${result.stderr}`.slice(0, 8000),
    );
  }
}

/** Starts one built application and waits for it to answer. */
export async function startApp(spec: AppSpec): Promise<RunningApp> {
  const child: ChildProcess = spawn("npm", ["run", spec.startScript], {
    cwd: spec.directory,
    env: { ...process.env, ...spec.environment, PORT: String(spec.port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout?.on("data", (chunk: Buffer) => {
    log(spec.name, chunk.toString("utf8"));
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    log(spec.name, chunk.toString("utf8"));
  });

  const exited = new Promise<never>((_resolve, reject) => {
    child.once("exit", (code) => {
      reject(new Error(`${spec.name} exited with code ${String(code)} before it became ready`));
    });
  });

  await Promise.race([waitForUrl(spec.readyUrl, 120_000), exited]);
  child.removeAllListeners("exit");

  return {
    name: spec.name,
    close: (): Promise<void> =>
      new Promise<void>((resolve) => {
        if (child.exitCode !== null || child.signalCode !== null) {
          resolve();
          return;
        }
        child.once("exit", () => {
          resolve();
        });
        child.kill("SIGTERM");
        setTimeout(() => {
          child.kill("SIGKILL");
        }, 5_000).unref();
      }),
  };
}

/** Polls `url` until it answers with any status, or the budget runs out. */
export async function waitForUrl(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastFailure = "no attempt was made";
  while (Date.now() < deadline) {
    try {
      await fetch(url, { redirect: "manual" });
      return;
    } catch (cause) {
      lastFailure = cause instanceof Error ? cause.message : String(cause);
      await new Promise<void>((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`${url} never answered within ${String(timeoutMs)}ms; last failure: ${lastFailure}`);
}
