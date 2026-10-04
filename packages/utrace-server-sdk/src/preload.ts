/**
 * Preload entry: `node --import @utrace/server-sdk/preload`.
 *
 * Node awaits this module's evaluation before it loads the application entry
 * point, so the top-level await below finishes activation first. Without a
 * prepared-preview bootstrap credential it resolves immediately and the process
 * stays dormant: no hooks, no provider, no connections, no exporter.
 *
 * Activation failure rejects here on purpose. A prepared preview that cannot be
 * observed must not boot as an unobserved substitute.
 */

import { register } from './register.js';

await register();
