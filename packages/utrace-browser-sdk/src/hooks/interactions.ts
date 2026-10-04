/**
 * Interaction, focus, input, and form capture.
 *
 * The runtime records semantic product events, not an unbounded copy of native
 * browser events: one activation for a completed user or agent action, explicit
 * focus changes, bounded input metadata, and form submission.
 *
 * Input observation reports target, edit class, length bucket, validity, and
 * changed state. It never emits a keystroke stream or an unapproved field
 * value, so it listens to `change` rather than `input`.
 *
 * Actor attribution uses `Event.isTrusted`: a trusted event is user activity,
 * an untrusted one is scripted and is attributed to the agent.
 */

import { lengthBucket } from "../redaction/classes.js";
import type {
  Actor,
  FocusChangePayload,
  FormSubmissionPayload,
  InputEditClass,
  InputMetadataPayload,
  InteractionActivationPayload,
  SafeValue,
} from "../observation/types.js";
import { readElementAnnotations, resolveSurface } from "../annotations/attributes.js";
import { computeAccessibleName, computeRole } from "../registry/accessible-name.js";
import { isPlatformExcludedElement } from "../redaction/platform-exclusions.js";
import { toSafeValue } from "../redaction/pipeline.js";
import type { RuntimeContext } from "../runtime/context.js";
import type { HookUninstall } from "./install.js";

const ACTIVATABLE_ROLES: readonly string[] = [
  "button",
  "link",
  "checkbox",
  "radio",
  "switch",
  "tab",
  "menuitem",
  "option",
  "treeitem",
];

const actorOf = (event: Event): Actor => (event.isTrusted ? "user" : "agent");

const closestActivatable = (target: EventTarget | null): Element | null => {
  if (!(target instanceof Element)) return null;
  let current: Element | null = target;
  while (current !== null) {
    if (current.hasAttribute("data-utrace-target") || current.hasAttribute("data-utrace-visual-target")) return current;
    if (ACTIVATABLE_ROLES.includes(computeRole(current))) return current;
    current = current.parentElement;
  }
  return null;
};

const labelFor = (context: RuntimeContext, element: Element, sensitive: boolean): SafeValue | undefined => {
  const source = computeAccessibleName(element);
  if (source === null) return undefined;
  return toSafeValue(
    context.redaction.apply({
      kind: "accessible_label",
      value: source,
      element,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: readElementAnnotations(element, context.manifest).safeValueRuleId,
      annotatedSensitive: sensitive,
    }),
  );
};

const editClassFor = (element: Element, previousLength: number, nextLength: number): InputEditClass => {
  if (element instanceof HTMLSelectElement) return "select_option";
  if (element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio")) return "toggle";
  if (nextLength === 0) return "clear";
  if (previousLength === 0) return "insert";
  if (nextLength > previousLength) return "insert";
  if (nextLength < previousLength) return "delete";
  return "replace";
};

const validityOf = (element: Element): InputMetadataPayload["validity"] => {
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
    return element.validity.valid ? "valid" : "invalid";
  }
  return "unchecked";
};

const valueLength = (element: Element): number => {
  if (element instanceof HTMLInputElement) return element.type === "checkbox" || element.type === "radio" ? 0 : element.value.length;
  if (element instanceof HTMLTextAreaElement) return element.value.length;
  if (element instanceof HTMLSelectElement) return element.value.length;
  return 0;
};

export const installInteractionHook = (context: RuntimeContext): HookUninstall => {
  const doc = context.view.document;
  const previousLengths = new WeakMap<Element, number>();

  const onClick = (event: MouseEvent): void => {
    const element = closestActivatable(event.target);
    if (element === null) return;
    const surface = resolveSurface(element, context.manifest);
    const annotations = readElementAnnotations(element, context.manifest);
    const registration = context.registry.query(
      annotations.targetDefinitionId === null ? {} : { definition_id: annotations.targetDefinitionId },
      context.currentRoute().route_template,
    );
    const span = context.trace.startInteractionSpan();
    const payload: InteractionActivationPayload = {
      actor: actorOf(event),
      // `detail === 0` is Chrome's signal that the activation came from the
      // keyboard rather than a pointer.
      activation_kind: event.detail === 0 ? "keyboard" : "pointer",
      role: computeRole(element),
      label: labelFor(context, element, surface.sensitive),
      surface: surface.surface,
    };
    context.emit(
      { type: "interaction.activation", body: payload },
      {
        targetId: registration.status === "found" ? registration.target.target_id : null,
        targetRevision: registration.status === "found" ? registration.target.target_revision : null,
        traceId: span.trace_id,
        spanId: span.span_id,
      },
    );
  };

  const onFocusIn = (event: FocusEvent): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const surface = resolveSurface(target, context.manifest);
    previousLengths.set(target, valueLength(target));
    const payload: FocusChangePayload = {
      actor: actorOf(event),
      role: computeRole(target),
      label: labelFor(context, target, surface.sensitive),
      surface: surface.surface,
    };
    context.emit({ type: "interaction.focus_change", body: payload }, {
      targetId: null,
      targetRevision: null,
      traceId: null,
      spanId: null,
    });
  };

  const onChange = (event: Event): void => {
    const element = event.target;
    if (!(element instanceof Element)) return;
    const surface = resolveSurface(element, context.manifest);
    const annotations = readElementAnnotations(element, context.manifest);
    const sensitive = surface.sensitive || annotations.sensitive || isPlatformExcludedElement(element);
    const previous = previousLengths.get(element) ?? 0;
    const next = valueLength(element);
    previousLengths.set(element, next);
    const approved =
      annotations.safeValueRuleId === null || sensitive
        ? undefined
        : toSafeValue(
            context.redaction.apply({
              kind: "form_value",
              value: element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement ? element.value : "",
              element,
              inAttestedDocument: true,
              rawBody: false,
              approvedRuleId: annotations.safeValueRuleId,
              annotatedSensitive: sensitive,
            }),
          );
    const payload: InputMetadataPayload = {
      actor: actorOf(event),
      role: computeRole(element),
      field_name: labelFor(context, element, sensitive),
      edit_class: editClassFor(element, previous, next),
      length_bucket: lengthBucket(next),
      validity: validityOf(element),
      changed: previous !== next || element instanceof HTMLInputElement,
      sensitive_field: sensitive,
      approved_value: approved,
    };
    context.emit({ type: "input.metadata", body: payload }, {
      targetId: null,
      targetRevision: null,
      traceId: null,
      spanId: null,
    });
  };

  const onSubmit = (event: SubmitEvent): void => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const surface = resolveSurface(form, context.manifest);
    let invalid = 0;
    for (const control of form.elements) {
      if (
        (control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement || control instanceof HTMLSelectElement) &&
        !control.validity.valid
      ) {
        invalid += 1;
      }
    }
    const method = form.method.toLowerCase();
    const payload: FormSubmissionPayload = {
      actor: actorOf(event),
      form_name: labelFor(context, form, surface.sensitive),
      method: method === "post" || method === "dialog" ? method : "get",
      field_count: form.elements.length,
      invalid_field_count: invalid,
      surface: surface.surface,
    };
    context.emit({ type: "form.submission", body: payload }, {
      targetId: null,
      targetRevision: null,
      traceId: null,
      spanId: null,
    });
  };

  doc.addEventListener("click", onClick, true);
  doc.addEventListener("focusin", onFocusIn, true);
  doc.addEventListener("change", onChange, true);
  doc.addEventListener("submit", onSubmit, true);
  return (): void => {
    doc.removeEventListener("click", onClick, true);
    doc.removeEventListener("focusin", onFocusIn, true);
    doc.removeEventListener("change", onChange, true);
    doc.removeEventListener("submit", onSubmit, true);
  };
};
