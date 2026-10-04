/**
 * Where the language-neutral contract assets live.
 *
 * The JSON Schemas and fixtures are shared by both bindings, so they sit beside
 * them in the package directory rather than inside either language's tree. Both
 * bindings resolve them from their own source location; there is no
 * configuration and no search path.
 */

import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("../..", import.meta.url));

/** `packages/utrace-contracts/schemas` — one directory per contract area. */
export const SCHEMAS_ROOT = `${packageRoot}schemas`;

/** `packages/utrace-contracts/fixtures` — `<area>/valid` and `<area>/invalid`. */
export const FIXTURES_ROOT = `${packageRoot}fixtures`;
