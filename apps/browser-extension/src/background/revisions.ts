/**
 * Which pushed session state the service worker keeps.
 *
 * Readiness and character state each carry a revision that is monotonic within
 * one Interview Session. The control plane pushes them whenever they change and
 * again on every reconnect, so a frame older than the one held for the same
 * session is stale, while a frame for another session starts that session's
 * revisions afresh.
 */
export type SessionRevision = Readonly<{ sessionId: string; revision: number }>;

export function supersedes(held: SessionRevision | null, incoming: SessionRevision): boolean {
  return (
    held === null || held.sessionId !== incoming.sessionId || incoming.revision >= held.revision
  );
}
