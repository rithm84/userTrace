/**
 * `server-only` throws when a bundler resolves it for the browser, which is how
 * it keeps server modules out of client bundles. Vitest runs those same modules
 * in jsdom on purpose, so it resolves to this empty module instead. The real
 * guard still applies to `next build`.
 */
export {};
