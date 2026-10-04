/**
 * Bootstrap entry for CommonJS and custom startup paths.
 *
 * `register()` must be awaited before the application's own modules are
 * loaded, so instrumentation is installed before anything it patches is
 * required. ESM bootstraps should prefer `node --import @utrace/server-sdk/preload`.
 */

import { register as registerModuleHooks } from 'node:module';

import { activateServerSdk, defaultOptions, type ServerSdkHandle } from './sdk.js';

let handle: ServerSdkHandle | undefined;
let pending: Promise<ServerSdkHandle> | undefined;
let shutdownHooked = false;
let esmHooked = false;

/**
 * Installs the ESM module hook so `import` graphs are patched too.
 *
 * It runs only for an activated preview, after activation and before the
 * application entry point loads, so a dormant process installs no hooks at all.
 */
function installEsmHook(active: ServerSdkHandle): void {
  if (esmHooked || active.state === 'dormant') {
    return;
  }
  esmHooked = true;
  registerModuleHooks('import-in-the-middle/hook.mjs', import.meta.url);
}

function installShutdownHooks(active: ServerSdkHandle): void {
  if (shutdownHooked || active.state === 'dormant') {
    return;
  }
  shutdownHooked = true;
  const graceful = (): void => {
    void active.shutdown();
  };
  process.once('SIGTERM', graceful);
  process.once('SIGINT', graceful);
  process.once('beforeExit', graceful);
}

/**
 * Activates the SDK exactly once per process.
 *
 * A second call returns the same handle; two independent bootstraps in one
 * process would be a duplicate owner, which the provider installation rejects.
 */
export async function register(): Promise<ServerSdkHandle> {
  if (handle !== undefined) {
    return handle;
  }
  if (pending !== undefined) {
    return pending;
  }
  pending = activateServerSdk(defaultOptions());
  try {
    handle = await pending;
  } finally {
    pending = undefined;
  }
  installEsmHook(handle);
  installShutdownHooks(handle);
  return handle;
}
