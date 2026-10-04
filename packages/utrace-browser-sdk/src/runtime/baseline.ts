/**
 * Baseline projection.
 *
 * A baseline contains redacted structure, semantics, mounted targets, route
 * state, and relevant product state. It is not a screenshot, a raw HTML copy,
 * or an unrestricted DOM or accessibility-tree dump. Automatic structure
 * capture is selective: page landmarks, relevant hierarchy, mounted interactive
 * elements, registered product entities and regions, structured targets, and
 * the minimum ancestor context needed to understand them.
 */

import type { BrowserView } from "../core/dom.js";
import { MAX_BASELINE_NODES } from "../core/limits.js";
import type { BaselineLandmark, BaselineReason, BaselineSnapshotPayload, SafeEntityRef } from "../observation/types.js";
import { readElementAnnotations, resolveSurface } from "../annotations/attributes.js";
import type { ManifestIndex } from "../annotations/manifest.js";
import { computeAccessibleName, computeRole } from "../registry/accessible-name.js";
import type { TargetRegistry } from "../registry/targets.js";
import type { RedactionPipeline } from "../redaction/pipeline.js";
import { toSafeValue } from "../redaction/pipeline.js";

const LANDMARK_SELECTOR = [
  "main",
  "nav",
  "header",
  "footer",
  "aside",
  "form",
  "dialog",
  "section[aria-label]",
  "section[aria-labelledby]",
  "[role=main]",
  "[role=navigation]",
  "[role=banner]",
  "[role=contentinfo]",
  "[role=complementary]",
  "[role=region]",
  "[role=dialog]",
  "[role=search]",
  "[data-utrace-surface]",
].join(",");

const depthOf = (element: Element, root: Element): number => {
  let depth = 0;
  let current: Element | null = element.parentElement;
  while (current !== null && current !== root) {
    depth += 1;
    current = current.parentElement;
  }
  return depth;
};

export interface BaselineInput {
  readonly baselineId: string;
  readonly reason: BaselineReason;
  readonly view: BrowserView;
  readonly manifest: ManifestIndex;
  readonly redaction: RedactionPipeline;
  readonly registry: TargetRegistry;
  readonly route: BaselineSnapshotPayload["route"];
}

export const buildBaseline = (input: BaselineInput): BaselineSnapshotPayload => {
  const root = input.view.document.documentElement;
  const landmarks: BaselineLandmark[] = [];
  const surfaces = new Set<string>();
  const entities: SafeEntityRef[] = [];
  const seenEntities = new Set<string>();
  let budgetExhausted = false;

  for (const element of input.view.document.querySelectorAll(LANDMARK_SELECTOR)) {
    if (landmarks.length >= MAX_BASELINE_NODES) {
      budgetExhausted = true;
      break;
    }
    const surface = resolveSurface(element, input.manifest);
    if (surface.surface !== undefined) surfaces.add(surface.surface);
    const nameSource = computeAccessibleName(element);
    landmarks.push({
      role: computeRole(element),
      label:
        nameSource === null
          ? undefined
          : toSafeValue(
              input.redaction.apply({
                kind: "accessible_label",
                value: nameSource,
                element,
                inAttestedDocument: true,
                rawBody: false,
                approvedRuleId: readElementAnnotations(element, input.manifest).safeValueRuleId,
                annotatedSensitive: surface.sensitive,
              }),
            ),
      depth: depthOf(element, root),
      child_count: element.childElementCount,
    });
  }

  for (const element of input.view.document.querySelectorAll("[data-utrace-entity]")) {
    const annotations = readElementAnnotations(element, input.manifest);
    const entity = annotations.entity;
    if (entity === null) continue;
    const outcome = input.redaction.apply({
      kind: "annotation_attribute",
      value: entity.instance_ref_raw,
      element,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: entity.instance_ref_rule_id,
      annotatedSensitive: annotations.sensitive,
    });
    if (outcome.value === null) continue;
    const key = `${entity.entity_definition_id}|${outcome.value}`;
    if (seenEntities.has(key)) continue;
    seenEntities.add(key);
    entities.push({ entity_definition_id: entity.entity_definition_id, instance_ref: outcome.value });
  }

  return {
    baseline_id: input.baselineId,
    reason: input.reason,
    route: input.route,
    landmarks,
    targets: input.registry.snapshot(),
    surfaces: [...surfaces],
    entities,
    node_budget_exhausted: budgetExhausted,
  };
};
