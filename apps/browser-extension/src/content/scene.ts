/**
 * Scene execution.
 *
 * A scene is atomic: one agent cursor plus ordered gestures and retained
 * marks. Every initial target must validate or the scene is rejected, and
 * visibility, occlusion, and target revision are checked again immediately
 * before each gesture. Circles and traces are drawn by the cursor along paths
 * derived from the current structured geometry — never from model-supplied
 * coordinates. A scene has one lifetime and clears as a unit.
 *
 * A mark that is not retained stays long enough to be seen and then fades, and
 * the cursor fades once nothing has moved it for a few seconds, so the agent's
 * gestures accompany what it says rather than piling up on the page. A newer
 * scene stops any older animation still running, so two never fight over the
 * cursor.
 */
import { animate } from "./animate";
import { AgentCursor } from "./cursor";
import { PageBridge } from "./bridgeClient";
import {
  centreOf,
  checkTarget,
  circlePath,
  labelBox,
  tracePath,
  type LabelPlacement,
} from "./sceneGeometry";
import type { SurfaceLayers } from "./host";
import type { ResolvedTarget } from "../protocol/bridge";
import type { CommandOutcome, TargetReference } from "../protocol/common";
import type { Scene, SceneStep, VisualCommandFrame } from "../protocol/control";

const SCROLL_SETTLE_MS = 320;
/**
 * The worker brings the bound tab to the front just before it routes a gesture,
 * and the document reports itself visible a moment later.
 */
const VISIBLE_WAIT_MS = 1_000;
const DRAW_DURATION_MS = 520;
const MARK_HOLD_MS = 2_500;
const MARK_FADE_MS = 400;
const CURSOR_IDLE_MS = 3_000;
const MARK_PADDING_PX = 8;
const LABEL_GAP_PX = 10;
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

export type CommandExecution = Readonly<{
  outcome: CommandOutcome;
  reason?: string;
  observedTargets: readonly TargetReference[];
}>;

export class SceneRunner {
  private readonly layers: SurfaceLayers;
  private readonly bridge: PageBridge;
  private readonly cursor: AgentCursor;
  private readonly retained = new Set<Element>();
  private runningCommandId: string | null = null;
  private withdrawnCommandId: string | null = null;
  /** Advances whenever the scene is cleared, ending every older animation. */
  private generation = 0;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(layers: SurfaceLayers, bridge: PageBridge, cursor: AgentCursor) {
    this.layers = layers;
    this.bridge = bridge;
    this.cursor = cursor;
  }

  /** Cancels the running command; the run loop reports `canceled`. */
  withdraw(commandId: string): void {
    if (this.runningCommandId === commandId) {
      this.withdrawnCommandId = commandId;
    }
  }

  /** Clears the cursor scene and every retained mark. */
  clear(): void {
    this.generation += 1;
    this.cancelIdle();
    for (const mark of this.retained) {
      mark.remove();
    }
    this.retained.clear();
    for (const child of [...this.layers.scene.children]) {
      child.remove();
    }
    for (const label of [...this.layers.cursor.querySelectorAll(".scene-label")]) {
      label.remove();
    }
    this.cursor.hide();
  }

  async execute(frame: VisualCommandFrame): Promise<CommandExecution> {
    if (!(await becomesVisible(VISIBLE_WAIT_MS))) {
      return { outcome: "failed", reason: "tab_not_active", observedTargets: [] };
    }

    this.runningCommandId = frame.payload.command_id;
    this.withdrawnCommandId = null;
    try {
      const { command } = frame.payload;
      if (command.type === "clear_scene") {
        this.clear();
        return { outcome: "applied", observedTargets: [] };
      }
      if (command.type === "scroll_into_view") {
        return await this.scrollIntoView(command.target_id, command.target_revision);
      }
      return await this.runScene(command.scene);
    } finally {
      this.runningCommandId = null;
    }
  }

  private async runScene(scene: Scene): Promise<CommandExecution> {
    const requested = requestedTargets(scene.steps);
    const resolved = await this.resolve([...requested.keys()]);
    if (!resolved.ok) {
      return { outcome: resolved.outcome, reason: resolved.reason, observedTargets: [] };
    }

    const observed: TargetReference[] = [];
    for (const [targetId, revision] of requested) {
      const target = resolved.targets.get(targetId);
      if (target === undefined) {
        return { outcome: "stale_target", reason: "target_not_found", observedTargets: observed };
      }
      observed.push({ target_id: target.target_id, target_revision: target.target_revision });
      if (target.target_revision !== revision) {
        return {
          outcome: "stale_target",
          reason: "target_revision_changed",
          observedTargets: observed,
        };
      }
      const check = checkTarget(target, viewport());
      if (!check.usable) {
        return { outcome: "rejected", reason: check.reason, observedTargets: observed };
      }
    }

    this.clear();
    this.cursor.show();

    for (const step of scene.steps) {
      if (this.withdrawnCommandId !== null) {
        this.clear();
        return { outcome: "canceled", reason: "withdrawn", observedTargets: observed };
      }
      if (step.step === "clear") {
        this.clear();
        this.cursor.show();
        continue;
      }

      const fresh = await this.refresh(step.target_id, step.target_revision, {
        requireVisible: true,
      });
      if (!fresh.ok) {
        this.clear();
        return { outcome: fresh.outcome, reason: fresh.reason, observedTargets: observed };
      }
      await this.perform(step, fresh.target);
    }

    this.hideWhenIdle();
    return { outcome: "applied", observedTargets: observed };
  }

  private hideWhenIdle(): void {
    this.cancelIdle();
    const generation = this.generation;
    this.idleTimer = setTimeout(() => {
      if (generation === this.generation) {
        this.cursor.hide();
      }
    }, CURSOR_IDLE_MS);
  }

  private cancelIdle(): void {
    if (this.idleTimer !== null) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }

  private async perform(step: SceneStep, target: ResolvedTarget): Promise<void> {
    const centre = centreOf(target.rect);
    switch (step.step) {
      case "move":
        await this.cursor.glideTo(centre);
        return;
      case "point":
        await this.cursor.glideTo(centre);
        await wait(step.dwell_ms);
        return;
      case "circle":
        await this.drawPath(circlePath(target.rect, MARK_PADDING_PX), step.retain);
        return;
      case "trace":
        await this.drawPath(
          tracePath(target.rect, step.edge, MARK_PADDING_PX),
          step.retain,
        );
        return;
      case "label":
        await this.cursor.glideTo(centre);
        this.drawLabel(step.text, step.placement, target, step.retain);
        return;
      case "clear":
        this.clear();
        return;
    }
  }

  /** Draws one stroke while the cursor travels along it. */
  private async drawPath(definition: string, retain: boolean): Promise<void> {
    const path = document.createElementNS(SVG_NAMESPACE, "path");
    path.setAttribute("class", "scene-mark coral");
    path.setAttribute("d", definition);
    path.dataset["utraceTestid"] = "scene-mark";
    this.layers.scene.append(path);

    const length = path.getTotalLength();
    path.style.strokeDasharray = String(length);
    path.style.strokeDashoffset = String(length);

    const generation = this.generation;
    await this.cursor.glideTo(pointAt(path, 0));
    await animate(
      DRAW_DURATION_MS,
      (progress) => {
        path.style.strokeDashoffset = String(length * (1 - progress));
        this.cursor.jumpTo(pointAt(path, length * progress));
      },
      () => generation === this.generation,
    );

    if (retain) {
      this.retained.add(path);
      return;
    }
    setTimeout(() => {
      path.style.transition = `opacity ${String(MARK_FADE_MS)}ms linear`;
      path.style.opacity = "0";
      setTimeout(() => {
        path.remove();
      }, MARK_FADE_MS);
    }, MARK_HOLD_MS);
  }

  private drawLabel(
    text: string,
    placement: LabelPlacement,
    target: ResolvedTarget,
    retain: boolean,
  ): void {
    const label = document.createElement("div");
    label.className = "scene-label";
    label.dataset["utraceTestid"] = "scene-label";
    label.textContent = text;
    label.style.visibility = "hidden";
    this.layers.cursor.append(label);

    const measured = label.getBoundingClientRect();
    const box = labelBox(
      target.rect,
      placement,
      { width: measured.width, height: measured.height },
      viewport(),
      LABEL_GAP_PX,
    );
    label.style.left = `${String(box.x)}px`;
    label.style.top = `${String(box.y)}px`;
    label.style.visibility = "visible";

    if (retain) {
      this.retained.add(label);
      return;
    }
    setTimeout(() => {
      label.remove();
    }, 1_200);
  }

  /**
   * Scrolls a registered target into view. Only the window is scrolled, which
   * is presentation of an already registered target rather than navigation; a
   * target held inside a product scroll container that the window cannot reach
   * fails explicitly instead of being reported as shown.
   */
  private async scrollIntoView(
    targetId: string,
    targetRevision: number,
  ): Promise<CommandExecution> {
    const fresh = await this.refresh(targetId, targetRevision, { requireVisible: false });
    if (!fresh.ok) {
      return { outcome: fresh.outcome, reason: fresh.reason, observedTargets: [] };
    }

    const { rect } = fresh.target;
    const view = viewport();
    window.scrollBy({
      top: rect.y + rect.height / 2 - view.height / 2,
      left: 0,
      behavior: "smooth",
    });
    await wait(SCROLL_SETTLE_MS);

    const settled = await this.refresh(targetId, targetRevision, { requireVisible: true });
    const observed: TargetReference[] = [
      { target_id: targetId, target_revision: targetRevision },
    ];
    if (!settled.ok) {
      return {
        outcome: "failed",
        reason: "target_not_scrolled_into_view",
        observedTargets: observed,
      };
    }
    return { outcome: "applied", observedTargets: observed };
  }

  private async resolve(
    targetIds: readonly string[],
  ): Promise<
    | Readonly<{ ok: true; targets: ReadonlyMap<string, ResolvedTarget> }>
    | Readonly<{ ok: false; outcome: CommandOutcome; reason: string }>
  > {
    const geometry = await this.bridge.queryTargets(targetIds);
    if (geometry.document_attachment_id !== this.bridge.documentAttachmentId) {
      return { ok: false, outcome: "stale_target", reason: "document_attachment_replaced" };
    }
    if (geometry.ambiguous.length > 0) {
      return { ok: false, outcome: "rejected", reason: "ambiguous_target" };
    }
    if (geometry.not_found.length > 0) {
      return { ok: false, outcome: "stale_target", reason: "target_not_found" };
    }
    return {
      ok: true,
      targets: new Map(geometry.targets.map((target) => [target.target_id, target])),
    };
  }

  private async refresh(
    targetId: string,
    targetRevision: number,
    options: Readonly<{ requireVisible: boolean }>,
  ): Promise<
    | Readonly<{ ok: true; target: ResolvedTarget }>
    | Readonly<{ ok: false; outcome: CommandOutcome; reason: string }>
  > {
    const resolved = await this.resolve([targetId]);
    if (!resolved.ok) {
      return resolved;
    }
    const target = resolved.targets.get(targetId);
    if (target === undefined) {
      return { ok: false, outcome: "stale_target", reason: "target_not_found" };
    }
    if (target.target_revision !== targetRevision) {
      return { ok: false, outcome: "stale_target", reason: "target_revision_changed" };
    }
    if (options.requireVisible) {
      const check = checkTarget(target, viewport());
      if (!check.usable) {
        return { ok: false, outcome: "rejected", reason: check.reason };
      }
    }
    return { ok: true, target };
  }
}

/** The exact target revision each step addresses, in first-seen order. */
function requestedTargets(steps: readonly SceneStep[]): ReadonlyMap<string, number> {
  const requested = new Map<string, number>();
  for (const step of steps) {
    if (step.step === "clear") {
      continue;
    }
    requested.set(step.target_id, step.target_revision);
  }
  return requested;
}

function pointAt(path: SVGPathElement, length: number): { x: number; y: number } {
  const point = path.getPointAtLength(length);
  return { x: point.x, y: point.y };
}

function viewport(): { width: number; height: number } {
  return { width: window.innerWidth, height: window.innerHeight };
}

/** Whether the document is visible now or becomes visible within `milliseconds`. */
function becomesVisible(milliseconds: number): Promise<boolean> {
  if (document.visibilityState === "visible") {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const settle = (): void => {
      if (document.visibilityState !== "visible") {
        return;
      }
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", settle);
      resolve(true);
    };
    const timer = setTimeout(() => {
      document.removeEventListener("visibilitychange", settle);
      resolve(false);
    }, milliseconds);
    document.addEventListener("visibilitychange", settle);
  });
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}
