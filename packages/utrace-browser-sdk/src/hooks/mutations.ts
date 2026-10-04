/**
 * Coalesced DOM mutation capture.
 *
 * DOM changes are coalesced into bounded semantic deltas rather than a stream
 * of raw mutation records. Raw HTML, scripts, arbitrary attributes, class
 * lists, computed styles, hidden content, and unrestricted page text are
 * excluded; only role, surface, registered target identity, and a redacted
 * label survive.
 */

import { DOM_COALESCE_WINDOW_MS, MAX_DOM_CHANGES_PER_RECORD } from "../core/limits.js";
import type { DomChange, DomChangePayload, SafeValue } from "../observation/types.js";
import { readElementAnnotations, resolveSurface } from "../annotations/attributes.js";
import { computeAccessibleName, computeRole } from "../registry/accessible-name.js";
import { toSafeValue } from "../redaction/pipeline.js";
import type { RuntimeContext } from "../runtime/context.js";
import { NO_TARGET_OR_TRACE } from "../runtime/context.js";
import type { HookUninstall } from "./install.js";

/** Attributes whose change is semantically meaningful. Everything else is ignored. */
const OBSERVED_ATTRIBUTES: readonly string[] = [
  "aria-busy",
  "aria-checked",
  "aria-disabled",
  "aria-expanded",
  "aria-hidden",
  "aria-invalid",
  "aria-label",
  "aria-selected",
  "disabled",
  "hidden",
  "role",
  "data-utrace-entity",
  "data-utrace-milestone",
  "data-utrace-sensitive",
  "data-utrace-surface",
  "data-utrace-target",
  "data-utrace-visual-target",
];

const labelOf = (context: RuntimeContext, element: Element): SafeValue | undefined => {
  const source = computeAccessibleName(element);
  if (source === null) return undefined;
  const surface = resolveSurface(element, context.manifest);
  return toSafeValue(
    context.redaction.apply({
      kind: "accessible_label",
      value: source,
      element,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: readElementAnnotations(element, context.manifest).safeValueRuleId,
      annotatedSensitive: surface.sensitive,
    }),
  );
};

const describeElement = (context: RuntimeContext, element: Element, kind: DomChange["kind"]): DomChange => ({
  kind,
  role: computeRole(element),
  surface: resolveSurface(element, context.manifest).surface,
  label: labelOf(context, element),
});

export const installMutationHook = (context: RuntimeContext): HookUninstall => {
  const view = context.view;
  let pending: DomChange[] = [];
  let truncated = false;
  let windowStart = context.clock.monotonicNowMs();
  let coalesced = 0;
  let timer: number | null = null;

  const flush = (): void => {
    timer = null;
    if (pending.length === 0) {
      coalesced = 0;
      truncated = false;
      return;
    }
    const payload: DomChangePayload = {
      changes: pending,
      coalesced_count: coalesced,
      window_start_offset: Math.round(windowStart),
      window_end_offset: Math.round(context.clock.monotonicNowMs()),
      truncated,
    };
    pending = [];
    truncated = false;
    coalesced = 0;
    windowStart = context.clock.monotonicNowMs();
    context.emit({ type: "dom.change", body: payload }, NO_TARGET_OR_TRACE);
  };

  const push = (change: DomChange): void => {
    coalesced += 1;
    if (pending.length >= MAX_DOM_CHANGES_PER_RECORD) {
      truncated = true;
      return;
    }
    pending.push(change);
  };

  const observer = new view.MutationObserver((records: readonly MutationRecord[]) => {
    context.onDomMutation(records);
    for (const record of records) {
      if (record.type === "childList") {
        for (const node of record.addedNodes) {
          if (node instanceof Element) push(describeElement(context, node, "added"));
        }
        for (const node of record.removedNodes) {
          if (node instanceof Element) push(describeElement(context, node, "removed"));
        }
        continue;
      }
      if (record.type === "characterData") {
        const parent = record.target.parentElement;
        if (parent !== null) push(describeElement(context, parent, "text"));
        continue;
      }
      const target = record.target;
      if (!(target instanceof Element)) continue;
      const attribute = record.attributeName ?? "";
      push(describeElement(context, target, attribute.startsWith("aria-") || attribute === "disabled" ? "state" : "attribute"));
    }
    if (timer === null) timer = view.setTimeout(flush, DOM_COALESCE_WINDOW_MS);
  });

  observer.observe(view.document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: [...OBSERVED_ATTRIBUTES],
  });

  return (): void => {
    observer.disconnect();
    if (timer !== null) view.clearTimeout(timer);
  };
};
