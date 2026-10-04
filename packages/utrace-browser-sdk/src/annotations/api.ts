/**
 * The typed annotation API.
 *
 * Application code may instantiate registered definitions; it cannot invent a
 * definition or grant a new observation scope. Every call is checked against
 * the activation's signed manifest and fails closed by returning a typed
 * rejection rather than throwing into the product's render path — an annotation
 * mistake must not break the client application inside a live preview.
 */

import type { Actor, MilestoneAttribute, SafeValue } from "../observation/types.js";
import { NO_TARGET_OR_TRACE } from "../runtime/context.js";
import type { SharedObservationRuntime } from "../runtime/runtime.js";
import { toSafeValue } from "../redaction/pipeline.js";
import type { TargetRegistration } from "../registry/targets.js";
import { readElementAnnotations } from "./attributes.js";

export type AnnotationRejection =
  | "unknown_definition"
  | "unknown_severity"
  | "unknown_priority"
  | "unknown_safe_value_rule"
  | "runtime_not_ready";

export type AnnotationResult<T> =
  | { readonly accepted: true; readonly value: T }
  | { readonly accepted: false; readonly reason: AnnotationRejection };

export interface TargetRegistrationRequest {
  readonly element: Element;
  readonly definitionId: string;
}

export interface MilestoneRequest {
  readonly milestoneId: string;
  readonly severity: string | null;
  readonly priority: string | null;
  readonly entityDefinitionId: string | null;
  readonly entityInstanceRef: string | null;
  readonly attributes: readonly MilestoneAttributeRequest[];
}

export interface MilestoneAttributeRequest {
  readonly name: string;
  readonly ruleId: string;
  readonly value: string;
}

/**
 * The annotation surface a client application calls. The production profile
 * supplies an inert implementation of the same interface, so annotated call
 * sites compile and do nothing outside a prepared preview.
 */
export interface AnnotationSurface {
  readonly registerTarget: (request: TargetRegistrationRequest) => AnnotationResult<TargetRegistration>;
  readonly markSensitive: (element: Element) => void;
  readonly safeValue: (ruleId: string, value: string) => AnnotationResult<SafeValue>;
  readonly milestone: (request: MilestoneRequest) => AnnotationResult<null>;
  readonly setActor: (actor: Actor) => void;
}

/** The inert surface. It grants no scope and produces no observation. */
export const inertAnnotationSurface = (): AnnotationSurface => ({
  registerTarget: (): AnnotationResult<TargetRegistration> => ({ accepted: false, reason: "runtime_not_ready" }),
  markSensitive: (): void => undefined,
  safeValue: (): AnnotationResult<SafeValue> => ({ accepted: false, reason: "runtime_not_ready" }),
  milestone: (): AnnotationResult<null> => ({ accepted: false, reason: "runtime_not_ready" }),
  setActor: (): void => undefined,
});

export class AnnotationApi implements AnnotationSurface {
  readonly #runtime: SharedObservationRuntime;

  constructor(runtime: SharedObservationRuntime) {
    this.#runtime = runtime;
  }

  /**
   * Registers a component's target. The returned handle preserves the same
   * instance across ordinary rerenders; `unmount` is terminal.
   */
  registerTarget = (request: TargetRegistrationRequest): AnnotationResult<TargetRegistration> => {
    if (this.#runtime.manifest.target(request.definitionId) === null) {
      return { accepted: false, reason: "unknown_definition" };
    }
    const annotations = readElementAnnotations(request.element, this.#runtime.manifest);
    const registration = this.#runtime.registry.register(request.element, request.definitionId, annotations);
    if (registration === null) return { accepted: false, reason: "unknown_definition" };
    return { accepted: true, value: registration };
  };

  /** Marks a subtree sensitive. The taint survives reparenting until destruction. */
  markSensitive = (element: Element): void => {
    element.setAttribute("data-utrace-sensitive", "true");
    this.#runtime.markSensitiveSubtree(element);
  };

  /** Produces the safe representation of one client value under a signed rule. */
  safeValue = (ruleId: string, value: string): AnnotationResult<SafeValue> => {
    const rule = this.#runtime.manifest.manifest.redaction.safe_value_rules.find(
      (candidate) => candidate.rule_id === ruleId,
    );
    if (rule === undefined) return { accepted: false, reason: "unknown_safe_value_rule" };
    return {
      accepted: true,
      value: toSafeValue(
        this.#runtime.redaction.apply({
          kind: "annotation_attribute",
          value,
          element: null,
          inAttestedDocument: true,
          rawBody: false,
          approvedRuleId: ruleId,
          annotatedSensitive: false,
        }),
      ),
    };
  };

  /** Records an explicit workflow milestone. */
  milestone = (request: MilestoneRequest): AnnotationResult<null> => {
    const manifest = this.#runtime.manifest;
    const definition = manifest.milestone(request.milestoneId);
    if (definition === null) return { accepted: false, reason: "unknown_definition" };
    if (request.severity !== null && !manifest.severityAllowed(request.severity)) {
      return { accepted: false, reason: "unknown_severity" };
    }
    if (request.priority !== null && !manifest.priorityAllowed(request.priority)) {
      return { accepted: false, reason: "unknown_priority" };
    }
    const attributes: MilestoneAttribute[] = [];
    for (const attribute of request.attributes) {
      if (!definition.attribute_rule_ids.includes(attribute.ruleId)) {
        return { accepted: false, reason: "unknown_safe_value_rule" };
      }
      const safe = this.safeValue(attribute.ruleId, attribute.value);
      if (!safe.accepted) return safe;
      attributes.push({ name: attribute.name, value: safe.value });
    }
    const entityRef =
      request.entityDefinitionId === null || request.entityInstanceRef === null
        ? undefined
        : { entity_definition_id: request.entityDefinitionId, instance_ref: request.entityInstanceRef };
    if (entityRef !== undefined && manifest.entity(entityRef.entity_definition_id) === null) {
      return { accepted: false, reason: "unknown_definition" };
    }
    this.#runtime.emit(
      {
        type: "product.milestone",
        body: {
          milestone_id: request.milestoneId,
          severity: request.severity ?? undefined,
          priority: request.priority ?? undefined,
          entity_ref: entityRef,
          attributes,
          actor: this.#runtime.actor(),
        },
      },
      NO_TARGET_OR_TRACE,
    );
    return { accepted: true, value: null };
  };

  /** Attributes subsequent non-event observations to the user or the agent. */
  setActor = (actor: Actor): void => {
    this.#runtime.setActor(actor);
  };
}
