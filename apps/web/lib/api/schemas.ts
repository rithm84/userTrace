/**
 * The web application's side of the control-plane HTTP contract.
 *
 * `apps/api/openapi.json` is the contract; these schemas mirror it, and
 * `lib/api/client.ts` parses every response through them before it reaches a
 * component, so a drifted field fails loudly at the boundary instead of
 * rendering as an empty card. Vocabulary follows the PRD exactly and lifecycle
 * values stay snake_case.
 *
 * The definitions are split by the authority that serves them:
 *
 * - `./schemas/lifecycle` — the shared domain enumerations, checked against
 *   `packages/utrace-contracts/schemas/domain` by a test.
 * - `./schemas/staff` — the authenticated dashboard and Case File routes.
 * - `./schemas/session` — the participant routes and the durable session
 *   projection Supabase Realtime distributes.
 */

export * from "./schemas/lifecycle";
export * from "./schemas/staff";
export * from "./schemas/session";
