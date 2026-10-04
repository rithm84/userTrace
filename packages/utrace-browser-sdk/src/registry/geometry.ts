/**
 * Target geometry.
 *
 * The shared runtime is the sole source of the target identity and current
 * geometry the extension uses. Geometry is resolved to clipped top-level
 * viewport coordinates with accurate visibility and occlusion state, so the
 * runtime never implies that an offscreen, disabled, hidden, or covered target
 * is actionable.
 */

import type { BrowserView } from "../core/dom.js";
import type { TargetGeometry, TargetSemanticState } from "../observation/types.js";

/** Attribute marking the extension's own top-layer host, excluded from occlusion. */
export const EXTENSION_HOST_ATTRIBUTE = "data-utrace-extension-host";

const isHidden = (element: Element, view: BrowserView): boolean => {
  const style = view.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return true;
  if (Number.parseFloat(style.opacity) === 0) return true;
  return element.hasAttribute("hidden") || element.getAttribute("aria-hidden") === "true";
};

const isExtensionSurface = (node: Element | null): boolean => {
  let current: Element | null = node;
  while (current !== null) {
    if (current.hasAttribute(EXTENSION_HOST_ATTRIBUTE)) return true;
    current = current.parentElement;
  }
  return false;
};

const clip = (value: number, min: number, max: number): number => Math.min(Math.max(value, min), max);

/**
 * Occlusion probe: the element drawn on top at the target's centre and corners.
 * The extension's own surface is excluded from product-target occlusion checks.
 */
const isOccluded = (element: Element, rect: DOMRect, view: BrowserView): boolean => {
  const doc = view.document;
  const points: readonly (readonly [number, number])[] = [
    [rect.left + rect.width / 2, rect.top + rect.height / 2],
    [rect.left + 1, rect.top + 1],
    [rect.right - 1, rect.top + 1],
    [rect.left + 1, rect.bottom - 1],
    [rect.right - 1, rect.bottom - 1],
  ];
  let probed = 0;
  let covered = 0;
  for (const [x, y] of points) {
    if (x < 0 || y < 0 || x > view.innerWidth || y > view.innerHeight) continue;
    probed += 1;
    const hit = doc.elementFromPoint(x, y);
    if (hit === null) {
      covered += 1;
      continue;
    }
    if (isExtensionSurface(hit)) continue;
    if (hit !== element && !element.contains(hit) && !hit.contains(element)) covered += 1;
  }
  return probed > 0 && covered === probed;
};

export const measureGeometry = (element: Element, view: BrowserView): TargetGeometry => {
  const rect = element.getBoundingClientRect();
  const viewportWidth = view.innerWidth;
  const viewportHeight = view.innerHeight;
  const left = clip(rect.left, 0, viewportWidth);
  const top = clip(rect.top, 0, viewportHeight);
  const right = clip(rect.right, 0, viewportWidth);
  const bottom = clip(rect.bottom, 0, viewportHeight);
  const clipped = left !== rect.left || top !== rect.top || right !== rect.right || bottom !== rect.bottom;
  const hidden = isHidden(element, view);
  const emptyBox = rect.width === 0 || rect.height === 0;
  const intersects = right > left && bottom > top;
  const visibility = hidden || emptyBox ? "hidden" : intersects ? "visible" : "offscreen";
  const fullArea = rect.width * rect.height;
  const visibleArea = intersects ? (right - left) * (bottom - top) : 0;
  return {
    x: Math.round(left),
    y: Math.round(top),
    width: Math.round(right - left),
    height: Math.round(bottom - top),
    clipped,
    visibility,
    occluded: visibility === "visible" && isOccluded(element, rect, view),
    visible_fraction: visibility === "visible" && fullArea > 0 ? clip(visibleArea / fullArea, 0, 1) : 0,
    viewport_width: viewportWidth,
    viewport_height: viewportHeight,
  };
};

export const readSemanticState = (element: Element): TargetSemanticState => {
  const ariaBoolean = (name: string): boolean | undefined => {
    const value = element.getAttribute(name);
    if (value === null) return undefined;
    return value === "true";
  };
  const disabled =
    element.hasAttribute("disabled") ||
    element.getAttribute("aria-disabled") === "true" ||
    (element instanceof HTMLButtonElement && element.disabled) ||
    (element instanceof HTMLInputElement && element.disabled);
  const checked =
    element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio")
      ? element.checked
      : ariaBoolean("aria-checked");
  const invalid =
    element.getAttribute("aria-invalid") === "true" ||
    (element instanceof HTMLInputElement && !element.validity.valid) ||
    (element instanceof HTMLTextAreaElement && !element.validity.valid) ||
    (element instanceof HTMLSelectElement && !element.validity.valid);
  return {
    disabled,
    checked,
    expanded: ariaBoolean("aria-expanded"),
    selected: element instanceof HTMLOptionElement ? element.selected : ariaBoolean("aria-selected"),
    busy: element.getAttribute("aria-busy") === "true",
    invalid,
  };
};

export const geometryEquivalent = (left: TargetGeometry, right: TargetGeometry): boolean =>
  left.x === right.x &&
  left.y === right.y &&
  left.width === right.width &&
  left.height === right.height &&
  left.clipped === right.clipped &&
  left.visibility === right.visibility &&
  left.occluded === right.occluded;

export const semanticStateEquivalent = (left: TargetSemanticState, right: TargetSemanticState): boolean =>
  left.disabled === right.disabled &&
  left.checked === right.checked &&
  left.expanded === right.expanded &&
  left.selected === right.selected &&
  left.busy === right.busy &&
  left.invalid === right.invalid;
