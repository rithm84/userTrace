/**
 * Structured target registry.
 *
 * A target has two identities: a stable signed definition from the annotation
 * manifest, and a document-scoped mounted instance with an opaque `target_id`,
 * a monotonic `target_revision`, and a separate `layout_revision` for
 * geometry-only change. A registration handle survives an ordinary framework
 * rerender. Unmount is terminal and leaves a tombstone so a later command fails
 * specifically as `stale_target`. Identifiers are never reused in a document.
 */

import type { BrowserView } from "../core/dom.js";
import type { RandomSource } from "../core/ids.js";
import { opaqueId } from "../core/ids.js";
import type { SafeEntityRef, TargetGeometry, TargetSemanticState, TargetSummary } from "../observation/types.js";
import type { RedactionPipeline } from "../redaction/pipeline.js";
import { toSafeValue } from "../redaction/pipeline.js";
import type { ManifestIndex, TargetEffect } from "../annotations/manifest.js";
import type { ElementAnnotations } from "../annotations/attributes.js";
import { readElementAnnotations, resolveSurface } from "../annotations/attributes.js";
import { isPlatformExcludedElement } from "../redaction/platform-exclusions.js";
import { computeAccessibleName, computeRole } from "./accessible-name.js";
import { geometryEquivalent, measureGeometry, readSemanticState, semanticStateEquivalent } from "./geometry.js";

export type TargetChangeKind = "mounted" | "updated" | "unmounted" | "layout" | "rejected";

export interface TargetChange {
  readonly change: TargetChangeKind;
  readonly registry_revision: number;
  readonly target: TargetSummary | null;
  readonly rejected_annotation: string | null;
}

export type TargetChangeListener = (change: TargetChange) => void;

export interface TargetQuery {
  readonly definition_id?: string;
  readonly entity_ref?: SafeEntityRef;
  readonly effect?: TargetEffect;
  readonly route_template?: string;
  readonly visibility?: "visible" | "offscreen" | "any";
}

export type TargetQueryResult =
  | { readonly status: "found"; readonly target: TargetSummary }
  | { readonly status: "not_found" }
  | { readonly status: "ambiguous"; readonly match_count: number }
  | { readonly status: "stale_target"; readonly target_id: string };

interface MountedTarget {
  readonly target_id: string;
  readonly definition_id: string | null;
  readonly visual_only: boolean;
  readonly effects: readonly TargetEffect[];
  element: Element;
  target_revision: number;
  layout_revision: number;
  role: string;
  labelSource: string | null;
  entity: SafeEntityRef | null;
  state: TargetSemanticState;
  geometry: TargetGeometry;
}

/** Handle returned to client components. It preserves one instance across rerenders. */
export interface TargetRegistration {
  readonly targetId: string;
  /** Rebinds the same instance to a new element and bumps `target_revision`. */
  readonly rebind: (element: Element) => void;
  /** Recomputes semantics and geometry for this instance. */
  readonly refresh: () => void;
  /** Terminal. A later mount of the same definition receives a new identifier. */
  readonly unmount: () => void;
}

const AUTO_TARGET_SELECTOR = [
  "button",
  "a[href]",
  "input",
  "select",
  "textarea",
  "summary",
  "[role=button]",
  "[role=link]",
  "[role=checkbox]",
  "[role=switch]",
  "[role=tab]",
  "[role=menuitem]",
  "[role=option]",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "[role=heading]",
  "nav",
  "main",
  "header",
  "footer",
  "aside",
  "[role=region]",
  "table",
  "[role=table]",
  "form",
  "dialog",
  "[role=dialog]",
].join(",");

const VISUAL_ONLY_EFFECTS: readonly TargetEffect[] = ["visual_guidance"];

export class TargetRegistry {
  readonly #view: BrowserView;
  readonly #index: ManifestIndex;
  readonly #redaction: RedactionPipeline;
  readonly #random: RandomSource;
  readonly #mounted = new Map<string, MountedTarget>();
  readonly #byElement = new WeakMap<Element, MountedTarget>();
  readonly #tombstones = new Set<string>();
  readonly #listeners: TargetChangeListener[] = [];
  #registryRevision = 0;
  #ordinal = 0;

  constructor(view: BrowserView, index: ManifestIndex, redaction: RedactionPipeline, random: RandomSource) {
    this.#view = view;
    this.#index = index;
    this.#redaction = redaction;
    this.#random = random;
  }

  get registryRevision(): number {
    return this.#registryRevision;
  }

  onChange(listener: TargetChangeListener): void {
    this.#listeners.push(listener);
  }

  /** Registers an annotated target declared through the typed API or attributes. */
  register(element: Element, definitionId: string, annotations: ElementAnnotations): TargetRegistration | null {
    const definition = this.#index.target(definitionId);
    if (definition === null) {
      this.#emit({ change: "rejected", registry_revision: this.#bump(), target: null, rejected_annotation: definitionId });
      return null;
    }
    const existing = this.#byElement.get(element);
    if (existing !== undefined) return this.#handleFor(existing);
    const target = this.#mount(element, definitionId, definition.visual_only, definition.effects, annotations);
    return this.#handleFor(target);
  }

  /**
   * Creates automatic visual-only targets for safe native interactive elements
   * and meaningful semantic elements. These may be pointed to, circled,
   * labelled, or scrolled into view; they carry no product-action authority.
   */
  discoverVisualTargets(root: ParentNode): number {
    let created = 0;
    for (const element of root.querySelectorAll(AUTO_TARGET_SELECTOR)) {
      if (this.#byElement.has(element)) continue;
      // A declared target keeps its declared definition: claiming it here
      // would leave it visual-only and nameless, and a command could then
      // never address it by the definition the client annotated.
      if (element.hasAttribute("data-utrace-target") || element.hasAttribute("data-utrace-visual-target")) continue;
      if (isPlatformExcludedElement(element)) continue;
      if (computeAccessibleName(element) === null && computeRole(element) === "generic") continue;
      this.#mount(element, null, true, VISUAL_ONLY_EFFECTS, readElementAnnotations(element, this.#index));
      created += 1;
    }
    return created;
  }

  /** Recomputes every mounted instance and reports drift, semantic, and layout change. */
  refreshAll(): void {
    for (const target of [...this.#mounted.values()]) {
      if (!target.element.isConnected) {
        this.#unmount(target, "unmounted");
        continue;
      }
      this.#refresh(target);
    }
  }

  /** Structured query. Zero matches are `not_found`; several are `ambiguous`. */
  query(query: TargetQuery, currentRouteTemplate: string | undefined): TargetQueryResult {
    const matches: MountedTarget[] = [];
    for (const target of this.#mounted.values()) {
      if (query.definition_id !== undefined && target.definition_id !== query.definition_id) continue;
      if (query.effect !== undefined && !target.effects.includes(query.effect)) continue;
      if (query.entity_ref !== undefined) {
        const entity = target.entity;
        if (
          entity === null ||
          entity.entity_definition_id !== query.entity_ref.entity_definition_id ||
          entity.instance_ref !== query.entity_ref.instance_ref
        ) {
          continue;
        }
      }
      if (query.route_template !== undefined && query.route_template !== currentRouteTemplate) continue;
      if (query.visibility !== undefined && query.visibility !== "any" && target.geometry.visibility !== query.visibility) {
        continue;
      }
      matches.push(target);
    }
    const first = matches[0];
    if (first === undefined) return { status: "not_found" };
    if (matches.length > 1) return { status: "ambiguous", match_count: matches.length };
    return { status: "found", target: this.#summarize(first) };
  }

  /** Resolves one exact identifier, distinguishing unmounted from unknown. */
  byId(targetId: string): TargetQueryResult {
    const target = this.#mounted.get(targetId);
    if (target !== undefined) return { status: "found", target: this.#summarize(target) };
    if (this.#tombstones.has(targetId)) return { status: "stale_target", target_id: targetId };
    return { status: "not_found" };
  }

  elementFor(targetId: string): Element | null {
    return this.#mounted.get(targetId)?.element ?? null;
  }

  snapshot(): readonly TargetSummary[] {
    return [...this.#mounted.values()].map((target) => this.#summarize(target));
  }

  #handleFor(target: MountedTarget): TargetRegistration {
    return {
      targetId: target.target_id,
      rebind: (element: Element): void => {
        if (target.element === element) return;
        this.#byElement.delete(target.element);
        target.element = element;
        this.#byElement.set(element, target);
        target.target_revision += 1;
        this.#emit({
          change: "updated",
          registry_revision: this.#bump(),
          target: this.#summarize(target),
          rejected_annotation: null,
        });
      },
      refresh: (): void => {
        this.#refresh(target);
      },
      unmount: (): void => {
        this.#unmount(target, "unmounted");
      },
    };
  }

  #mount(
    element: Element,
    definitionId: string | null,
    visualOnly: boolean,
    effects: readonly TargetEffect[],
    annotations: ElementAnnotations,
  ): MountedTarget {
    this.#ordinal += 1;
    const target: MountedTarget = {
      target_id: opaqueId("tgt", this.#ordinal, this.#random),
      definition_id: definitionId,
      visual_only: visualOnly,
      effects,
      element,
      target_revision: 1,
      layout_revision: 1,
      role: computeRole(element),
      labelSource: computeAccessibleName(element),
      entity: this.#resolveEntity(element, annotations),
      state: readSemanticState(element),
      geometry: measureGeometry(element, this.#view),
    };
    this.#mounted.set(target.target_id, target);
    this.#byElement.set(element, target);
    for (const rejection of annotations.rejections) {
      this.#emit({
        change: "rejected",
        registry_revision: this.#bump(),
        target: null,
        rejected_annotation: `${rejection.attribute}:${rejection.reason}`,
      });
    }
    this.#emit({
      change: "mounted",
      registry_revision: this.#bump(),
      target: this.#summarize(target),
      rejected_annotation: null,
    });
    return target;
  }

  #refresh(target: MountedTarget): void {
    const role = computeRole(target.element);
    const labelSource = computeAccessibleName(target.element);
    const state = readSemanticState(target.element);
    const geometry = measureGeometry(target.element, this.#view);
    const semanticChanged =
      role !== target.role || labelSource !== target.labelSource || !semanticStateEquivalent(state, target.state);
    const displayChanged = geometry.visibility !== target.geometry.visibility || geometry.occluded !== target.geometry.occluded;
    const layoutChanged = !geometryEquivalent(geometry, target.geometry);

    target.role = role;
    target.labelSource = labelSource;
    target.state = state;
    target.geometry = geometry;

    if (semanticChanged || displayChanged) {
      target.target_revision += 1;
      target.layout_revision += 1;
      this.#emit({
        change: "updated",
        registry_revision: this.#bump(),
        target: this.#summarize(target),
        rejected_annotation: null,
      });
      return;
    }
    if (layoutChanged) {
      target.layout_revision += 1;
      this.#emit({
        change: "layout",
        registry_revision: this.#bump(),
        target: this.#summarize(target),
        rejected_annotation: null,
      });
    }
  }

  #unmount(target: MountedTarget, change: TargetChangeKind): void {
    this.#mounted.delete(target.target_id);
    this.#byElement.delete(target.element);
    this.#tombstones.add(target.target_id);
    this.#emit({
      change,
      registry_revision: this.#bump(),
      target: this.#summarize(target),
      rejected_annotation: null,
    });
  }

  #resolveEntity(element: Element, annotations: ElementAnnotations): SafeEntityRef | null {
    const entity = annotations.entity;
    if (entity === null) return null;
    const outcome = this.#redaction.apply({
      kind: "annotation_attribute",
      value: entity.instance_ref_raw,
      element,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: entity.instance_ref_rule_id,
      annotatedSensitive: annotations.sensitive,
    });
    if (outcome.value === null) return null;
    return { entity_definition_id: entity.entity_definition_id, instance_ref: outcome.value };
  }

  #summarize(target: MountedTarget): TargetSummary {
    const surface = resolveSurface(target.element, this.#index);
    const label =
      target.labelSource === null
        ? undefined
        : toSafeValue(
            this.#redaction.apply({
              kind: "accessible_label",
              value: target.labelSource,
              element: target.element,
              inAttestedDocument: true,
              rawBody: false,
              approvedRuleId: readElementAnnotations(target.element, this.#index).safeValueRuleId,
              annotatedSensitive: surface.sensitive,
            }),
          );
    return {
      target_id: target.target_id,
      target_revision: target.target_revision,
      layout_revision: target.layout_revision,
      definition_id: target.definition_id ?? undefined,
      visual_only: target.visual_only,
      role: target.role,
      label,
      entity_ref: target.entity ?? undefined,
      semantic_state: target.state,
      geometry: target.geometry,
    };
  }

  #bump(): number {
    this.#registryRevision += 1;
    return this.#registryRevision;
  }

  #emit(change: TargetChange): void {
    for (const listener of this.#listeners) listener(change);
  }
}
