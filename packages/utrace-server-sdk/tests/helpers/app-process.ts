/**
 * Runs a product service in its own process.
 *
 * The supported bootstrap path is `node --import @utrace/server-sdk/preload`,
 * so the tests start real applications that way and observe them only through
 * the control plane and the ingestion edge, exactly as uTrace would.
 */

import { spawn } from 'node:child_process';
import { request as httpRequest } from 'node:http';
import { join } from 'node:path';

const PACKAGE_ROOT = join(import.meta.dirname, '..', '..');

/** Built preload entry used with `node --import`. */
const PRELOAD_ENTRY = join(PACKAGE_ROOT, 'dist', 'preload.js');

/** A running application process. */
export interface AppProcess {
  readonly stdout: string[];
  readonly stderr: string[];
  readonly exited: Promise<number | null>;
  stop(): Promise<void>;
}

/** How to start one application process. */
export interface AppProcessOptions {
  readonly appFile: string;
  readonly environment: Readonly<Record<string, string>>;
  readonly withPreload: boolean;
  readonly expectListening?: boolean;
}

/** Spawns the application and resolves once it reports that it is listening. */
export async function startApp(options: AppProcessOptions): Promise<AppProcess> {
  const args = options.withPreload
    ? ['--import', PRELOAD_ENTRY, join(PACKAGE_ROOT, 'tests', 'apps', options.appFile)]
    : [join(PACKAGE_ROOT, 'tests', 'apps', options.appFile)];

  const child = spawn(process.execPath, args, {
    cwd: PACKAGE_ROOT,
    env: { PATH: process.env['PATH'] ?? '', ...options.environment },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  const stdout: string[] = [];
  const stderr: string[] = [];
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk: string) => {
    stdout.push(chunk);
  });
  child.stderr.on('data', (chunk: string) => {
    stderr.push(chunk);
  });

  const exited = new Promise<number | null>((resolve) => {
    child.once('exit', (code) => {
      resolve(code);
    });
  });

  const process_: AppProcess = {
    stdout,
    stderr,
    exited,
    stop: async (): Promise<void> => {
      if (child.exitCode === null) {
        child.kill('SIGTERM');
      }
      await Promise.race([
        exited,
        new Promise<void>((resolve) => {
          setTimeout(() => {
            child.kill('SIGKILL');
            resolve();
          }, 10_000).unref();
        }),
      ]);
    },
  };

  if (options.expectListening === false) {
    return process_;
  }

  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (stdout.join('').includes('APP_LISTENING')) {
      return process_;
    }
    if (child.exitCode !== null) {
      throw new Error(
        `application exited before listening (code ${String(child.exitCode)}): ${stderr.join('')}`,
      );
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  }
  await process_.stop();
  throw new Error(`application never reported listening: ${stderr.join('')}`);
}

/** One response from the product service under test. */
export interface AppResponse {
  readonly statusCode: number;
  readonly body: string;
}

/** Issues one request to the product service. */
export function callApp(
  port: number,
  path: string,
  headers: Readonly<Record<string, string>>,
): Promise<AppResponse> {
  return new Promise((resolve, reject) => {
    const request = httpRequest(
      { host: '127.0.0.1', port, path, method: 'GET', headers },
      (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => {
          chunks.push(chunk);
        });
        response.on('end', () => {
          resolve({
            statusCode: response.statusCode ?? 0,
            body: Buffer.concat(chunks).toString('utf8'),
          });
        });
      },
    );
    request.on('error', reject);
    request.end();
  });
}
