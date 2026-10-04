/**
 * History capture.
 *
 * Only committed route changes are reported. `pushState` and `replaceState` are
 * wrapped, and `popstate` covers back and forward navigation. The record
 * carries the normalized route template, never the raw path, query, or
 * fragment.
 */

import type { RuntimeContext } from "../runtime/context.js";
import type { HookUninstall } from "./install.js";
import { assertUnowned, markOwner } from "./install.js";

export const installHistoryHook = (context: RuntimeContext, runtimeId: string): HookUninstall => {
  const view = context.view;
  const history = view.history;
  const originalPush = history.pushState;
  const originalReplace = history.replaceState;
  assertUnowned("history", originalPush, runtimeId);

  const patchedPush = function patchedPush(this: History, data: unknown, unused: string, url?: string | URL | null): void {
    originalPush.call(this, data, unused, url);
    context.commitRouteChange("push", context.actor());
  };
  const patchedReplace = function patchedReplace(
    this: History,
    data: unknown,
    unused: string,
    url?: string | URL | null,
  ): void {
    originalReplace.call(this, data, unused, url);
    context.commitRouteChange("replace", context.actor());
  };
  const onPopState = (): void => {
    context.commitRouteChange("pop", "user");
  };

  markOwner(patchedPush, runtimeId);
  history.pushState = patchedPush;
  history.replaceState = patchedReplace;
  view.addEventListener("popstate", onPopState);

  return (): void => {
    history.pushState = originalPush;
    history.replaceState = originalReplace;
    view.removeEventListener("popstate", onPopState);
  };
};
