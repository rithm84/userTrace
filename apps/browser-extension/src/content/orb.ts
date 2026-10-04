/**
 * The orb and its four control pills.
 *
 * Hovering the centre circle expands Return to session above, Mute or Unmute
 * to the left, Pause or Resume to the right, and End interview below. Each
 * pill carries an icon and a short label; Mute and Pause show the action they
 * offer and turn coral tint while muted or paused, and End is the coral one.
 * Clicking the centre performs no action; pressing and dragging it moves the
 * orb and collapses the controls until release. The collapsed orb carries no
 * status text — status text belongs to the uTrace session tab.
 *
 * The controls open after a short hover intent, so a pointer passing over the
 * orb does not flash them, and close only after a grace period, so crossing
 * from one control to another does not flicker them. Keyboard focus on any
 * control opens them too. End asks for confirmation in a card placed where the
 * End pill was.
 *
 * Buttons reflect the session-control revision the control plane returns
 * rather than assuming success, and a duplicate click reuses the pending
 * action. Return to session is the only browser-local control and keeps
 * working while the control connection is down.
 */
import { ArrowLeft, Mic, MicOff, Pause, PhoneOff, Play, createElement, type IconNode } from "lucide";

import {
  confirmationOrigin,
  orbAnchor,
  orbCentre,
  orbRadius,
  pillOffsets,
  px,
  type Point,
  type Viewport,
} from "./orbGeometry";
import type { ControlAction } from "../protocol/common";
import type { ControlsState, OrbSettings } from "../shared/portMessages";

/** Travel before a press on the centre becomes a drag rather than a click. */
const DRAG_THRESHOLD_PX = 4;
const EXPAND_INTENT_MS = 90;
const COLLAPSE_GRACE_MS = 320;
const ICON_ATTRIBUTES = Object.freeze({
  width: 18,
  height: 18,
  "stroke-width": 2.5,
  "aria-hidden": "true",
  focusable: "false",
});

export type OrbCallbacks = Readonly<{
  onControlAction: (action: ControlAction) => void;
  onReturnToSession: () => void;
  onSettingsChanged: (settings: OrbSettings) => void;
}>;

type Pills = Readonly<{
  returnToSession: HTMLButtonElement;
  mute: HTMLButtonElement;
  pause: HTMLButtonElement;
  end: HTMLButtonElement;
}>;

type Confirmation = Readonly<{
  card: HTMLDivElement;
  keep: HTMLButtonElement;
  end: HTMLButtonElement;
}>;

/** A press on the centre, which becomes a drag once it travels far enough. */
type Press = Readonly<{ pointerId: number; start: Point; offset: Point }>;

export class AgentOrb {
  private readonly callbacks: OrbCallbacks;
  private readonly root: HTMLDivElement;
  private readonly orb: HTMLDivElement;
  private readonly canvasElement: HTMLCanvasElement;
  private readonly pills: Pills;
  private readonly confirmation: Confirmation;
  private settings: OrbSettings;
  private controls: ControlsState;
  private hovered = false;
  private keyboardFocused = false;
  private confirming = false;
  private dragging = false;
  private press: Press | null = null;
  private expandTimer: ReturnType<typeof setTimeout> | null = null;
  private collapseTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    container: HTMLElement,
    settings: OrbSettings,
    controls: ControlsState,
    callbacks: OrbCallbacks,
  ) {
    this.callbacks = callbacks;
    this.settings = settings;
    this.controls = controls;

    this.root = document.createElement("div");
    this.root.className = "orb-root";
    this.root.dataset["utraceTestid"] = "orb-root";
    this.root.dataset["expanded"] = "false";
    this.root.setAttribute("role", "group");
    this.root.setAttribute("aria-label", "uTrace agent controls");

    this.orb = document.createElement("div");
    this.orb.className = "orb";
    this.orb.dataset["utraceTestid"] = "orb";
    this.orb.setAttribute("role", "img");
    this.orb.setAttribute("aria-label", "uTrace agent");

    this.canvasElement = document.createElement("canvas");
    this.canvasElement.dataset["utraceTestid"] = "orb-canvas";
    this.canvasElement.setAttribute("aria-hidden", "true");
    this.orb.append(this.canvasElement);

    this.pills = {
      returnToSession: createPill("pill-top", "pill-return", ArrowLeft, "Return to session"),
      mute: createPill("pill-left", "pill-mute", Mic, "Mute"),
      pause: createPill("pill-right", "pill-pause", Pause, "Pause"),
      end: createPill("pill-bottom", "pill-end", PhoneOff, "End interview"),
    };
    this.pills.end.dataset["tone"] = "end";

    this.confirmation = this.createConfirmation();

    this.root.append(
      this.pills.returnToSession,
      this.pills.mute,
      this.pills.pause,
      this.pills.end,
      this.orb,
      this.confirmation.card,
    );
    container.append(this.root);

    this.bindDrag();
    this.bindHoverAndFocus();
    this.bindControls();
    this.applyLayout();
    this.applyControls(controls);

    window.addEventListener("resize", this.handleResize, { passive: true });
  }

  get canvas(): HTMLCanvasElement {
    return this.canvasElement;
  }

  applyControls(controls: ControlsState): void {
    this.controls = controls;
    const muted = controls.mute === "muted";
    const paused = controls.pause === "paused";
    const blocked =
      !controls.connected || controls.pause === "ended" || controls.pendingAction !== null;

    setPillContent(this.pills.mute, muted ? MicOff : Mic, muted ? "Unmute" : "Mute");
    this.pills.mute.dataset["state"] = controls.mute;
    setPillContent(this.pills.pause, paused ? Play : Pause, paused ? "Resume" : "Pause");
    this.pills.pause.dataset["state"] = controls.pause;

    this.pills.mute.disabled = blocked;
    this.pills.pause.disabled = blocked;
    this.pills.end.disabled = blocked;
    this.confirmation.end.disabled = blocked;
    this.pills.returnToSession.disabled = false;
  }

  applySettings(settings: OrbSettings): void {
    this.settings = settings;
    this.applyLayout();
  }

  destroy(): void {
    this.cancelExpand();
    this.cancelCollapse();
    window.removeEventListener("resize", this.handleResize);
    this.root.remove();
  }

  /** The controls show while hovered or keyboard-focused, never mid-drag or while End asks. */
  private render(): void {
    const expanded =
      (this.hovered || this.keyboardFocused) && !this.dragging && !this.confirming;
    this.root.dataset["expanded"] = String(expanded);
  }

  private cancelExpand(): void {
    if (this.expandTimer !== null) {
      clearTimeout(this.expandTimer);
      this.expandTimer = null;
    }
  }

  private cancelCollapse(): void {
    if (this.collapseTimer !== null) {
      clearTimeout(this.collapseTimer);
      this.collapseTimer = null;
    }
  }

  private createConfirmation(): Confirmation {
    const card = document.createElement("div");
    card.className = "confirm";
    card.dataset["utraceTestid"] = "end-confirmation";
    card.dataset["open"] = "false";
    card.setAttribute("role", "alertdialog");
    card.setAttribute("aria-labelledby", "utrace-end-question");
    card.setAttribute("aria-describedby", "utrace-end-detail");

    const question = document.createElement("p");
    question.className = "confirm-question";
    question.id = "utrace-end-question";
    question.textContent = "End the interview now?";

    const detail = document.createElement("p");
    detail.className = "confirm-detail";
    detail.id = "utrace-end-detail";
    detail.textContent =
      "Ending stops this case for good. To step away instead, choose Finish later in the session tab.";

    const keep = document.createElement("button");
    keep.type = "button";
    keep.textContent = "Keep going";
    keep.dataset["utraceTestid"] = "end-cancel";

    const end = document.createElement("button");
    end.type = "button";
    end.textContent = "End interview";
    end.dataset["tone"] = "end";
    end.dataset["utraceTestid"] = "end-confirm";

    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    actions.append(keep, end);
    card.append(question, detail, actions);
    return { card, keep, end };
  }

  private openConfirmation(): void {
    this.confirming = true;
    this.confirmation.card.dataset["open"] = "true";
    this.placeConfirmation(orbCentre(this.settings, this.viewport()));
    this.render();
    this.confirmation.keep.focus();
  }

  private closeConfirmation(): void {
    this.confirming = false;
    this.confirmation.card.dataset["open"] = "false";
    this.render();
  }

  private placeConfirmation(centre: Point): void {
    const card = this.confirmation.card;
    const origin = confirmationOrigin(
      centre,
      this.settings.diameter,
      { width: card.offsetWidth, height: card.offsetHeight },
      this.viewport(),
    );
    card.style.left = px(origin.x - centre.x);
    card.style.top = px(origin.y - centre.y);
  }

  private bindControls(): void {
    this.pills.returnToSession.addEventListener("click", () => {
      this.callbacks.onReturnToSession();
    });
    this.pills.mute.addEventListener("click", () => {
      this.callbacks.onControlAction(this.controls.mute === "muted" ? "unmute" : "mute");
    });
    this.pills.pause.addEventListener("click", () => {
      this.callbacks.onControlAction(this.controls.pause === "paused" ? "resume" : "pause");
    });
    this.pills.end.addEventListener("click", () => {
      this.openConfirmation();
    });
    this.confirmation.keep.addEventListener("click", () => {
      this.closeConfirmation();
      this.pills.end.focus();
    });
    this.confirmation.end.addEventListener("click", () => {
      this.closeConfirmation();
      this.callbacks.onControlAction("end");
    });
    this.root.addEventListener("keydown", (event: KeyboardEvent) => {
      if (event.key === "Escape" && this.confirming) {
        event.stopPropagation();
        this.closeConfirmation();
        this.pills.end.focus();
      }
    });
  }

  private bindHoverAndFocus(): void {
    this.root.addEventListener("pointerenter", () => {
      this.cancelCollapse();
      if (this.hovered || this.expandTimer !== null) {
        return;
      }
      this.expandTimer = setTimeout(() => {
        this.expandTimer = null;
        this.hovered = true;
        this.render();
      }, EXPAND_INTENT_MS);
    });
    this.root.addEventListener("pointerleave", () => {
      this.cancelExpand();
      this.cancelCollapse();
      this.collapseTimer = setTimeout(() => {
        this.collapseTimer = null;
        this.hovered = false;
        this.render();
      }, COLLAPSE_GRACE_MS);
    });
    // Keyboard focus opens the controls; focus a pointer click leaves behind
    // does not keep them open once the pointer has gone.
    this.root.addEventListener("focusin", (event: FocusEvent) => {
      const target = event.target;
      this.keyboardFocused = target instanceof Element && target.matches(":focus-visible");
      this.render();
    });
    this.root.addEventListener("focusout", (event: FocusEvent) => {
      const next = event.relatedTarget;
      if (next instanceof Node && this.root.contains(next)) {
        return;
      }
      this.keyboardFocused = false;
      this.render();
    });
  }

  private bindDrag(): void {
    this.orb.addEventListener("pointerdown", (event: PointerEvent) => {
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      const centre = orbCentre(this.settings, this.viewport());
      this.press = {
        pointerId: event.pointerId,
        start: { x: event.clientX, y: event.clientY },
        offset: { x: event.clientX - centre.x, y: event.clientY - centre.y },
      };
      this.orb.setPointerCapture(event.pointerId);
    });

    this.orb.addEventListener("pointermove", (event: PointerEvent) => {
      const press = this.press;
      if (press === null || press.pointerId !== event.pointerId) {
        return;
      }
      if (!this.dragging) {
        const travel = Math.hypot(event.clientX - press.start.x, event.clientY - press.start.y);
        if (travel < DRAG_THRESHOLD_PX) {
          return;
        }
        this.dragging = true;
        this.orb.dataset["dragging"] = "true";
        this.render();
      }
      this.settings = orbAnchor(
        { x: event.clientX - press.offset.x, y: event.clientY - press.offset.y },
        this.settings,
        this.viewport(),
      );
      this.applyLayout();
    });

    const release = (event: PointerEvent): void => {
      const press = this.press;
      if (press === null || press.pointerId !== event.pointerId) {
        return;
      }
      this.press = null;
      if (this.orb.hasPointerCapture(event.pointerId)) {
        this.orb.releasePointerCapture(event.pointerId);
      }
      if (!this.dragging) {
        return;
      }
      this.dragging = false;
      delete this.orb.dataset["dragging"];
      this.render();
      this.callbacks.onSettingsChanged(this.settings);
    };

    this.orb.addEventListener("pointerup", release);
    this.orb.addEventListener("pointercancel", release);
    // Clicking the centre performs no action; the listener only stops the
    // product from receiving the click.
    this.orb.addEventListener("click", (event: MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
    });
  }

  private handleResize = (): void => {
    this.applyLayout();
  };

  private viewport(): Viewport {
    return { width: window.innerWidth, height: window.innerHeight };
  }

  /** Writes the whole-pixel centre and offsets the stylesheet positions everything from. */
  private applyLayout(): void {
    const { diameter } = this.settings;
    const centre = orbCentre(this.settings, this.viewport());
    const offsets = pillOffsets(diameter);
    const style = this.root.style;
    style.left = px(centre.x);
    style.top = px(centre.y);
    style.setProperty("--diameter", px(diameter));
    style.setProperty("--radius", px(orbRadius(diameter)));
    style.setProperty("--stack-offset", px(offsets.stack));
    style.setProperty("--side-offset", px(offsets.side));
    if (this.confirming) {
      this.placeConfirmation(centre);
    }
  }
}

function createPill(
  positionClass: string,
  testId: string,
  icon: IconNode,
  label: string,
): HTMLButtonElement {
  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = `pill ${positionClass}`;
  pill.dataset["utraceTestid"] = testId;
  setPillContent(pill, icon, label);
  return pill;
}

/** A vector icon and the pill's label; the label alone names the button. */
function setPillContent(pill: HTMLButtonElement, icon: IconNode, label: string): void {
  const text = document.createElement("span");
  text.textContent = label;
  pill.replaceChildren(createElement(icon, ICON_ATTRIBUTES), text);
}
