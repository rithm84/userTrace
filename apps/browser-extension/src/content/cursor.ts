/**
 * The agent cursor.
 *
 * It is command-only and hidden between uses. It never follows, replaces, or
 * moves the customer's physical pointer, and its layer is click-through, so
 * the customer's own input is never blocked or intercepted.
 *
 * It moves like a hand rather than a pointer warping: it appears just beside
 * where it is going, then glides along a gentle curve that takes longer for
 * longer distances, easing in and out. With reduced motion requested, it jumps.
 */
import { animate } from "./animate";
import type { Point } from "./orbGeometry";

const CURSOR_SVG = `
<svg viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M3 2 L3 20 L8 15.5 L11.5 23 L15 21.2 L11.6 14 L18.5 13.4 Z"
        fill="#fcada3" stroke="#000000" stroke-width="2" stroke-linejoin="round" />
</svg>`;

const GLIDE_PX_PER_MS = 1.6;
const GLIDE_MIN_MS = 250;
const GLIDE_MAX_MS = 600;
/** How far the curve bows out, as a share of the distance travelled. */
const GLIDE_BOW = 0.12;
/** Where a freshly shown cursor appears, relative to its first destination. */
const ENTRY_OFFSET: Point = { x: -56, y: -40 };

export class AgentCursor {
  private readonly element: HTMLDivElement;
  private position: Point = { x: -100, y: -100 };
  private placed = false;
  /** Advances with every movement, so a newer one stops an older glide. */
  private motion = 0;

  constructor(layer: HTMLElement) {
    this.element = document.createElement("div");
    this.element.className = "agent-cursor";
    this.element.dataset["utraceTestid"] = "agent-cursor";
    this.element.dataset["visible"] = "false";
    this.element.innerHTML = CURSOR_SVG;
    layer.append(this.element);
  }

  get at(): Point {
    return this.position;
  }

  show(): void {
    this.element.dataset["visible"] = "true";
  }

  hide(): void {
    this.element.dataset["visible"] = "false";
    this.placed = false;
  }

  /** Places the cursor tip at a point at once, as when it traces a mark. */
  jumpTo(point: Point): void {
    this.motion += 1;
    this.place(point);
  }

  /** Glides the cursor tip to a point and resolves when it arrives. */
  glideTo(point: Point): Promise<void> {
    if (!this.placed) {
      this.place({ x: point.x + ENTRY_OFFSET.x, y: point.y + ENTRY_OFFSET.y });
    }
    this.motion += 1;
    const motion = this.motion;
    const from = this.position;
    const distance = Math.hypot(point.x - from.x, point.y - from.y);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || distance < 1) {
      this.place(point);
      return Promise.resolve();
    }
    const duration = Math.min(Math.max(distance / GLIDE_PX_PER_MS, GLIDE_MIN_MS), GLIDE_MAX_MS);
    // A control point off the straight line, to the left of travel, bows the path.
    const control = {
      x: (from.x + point.x) / 2 - (point.y - from.y) * GLIDE_BOW,
      y: (from.y + point.y) / 2 + (point.x - from.x) * GLIDE_BOW,
    };
    return animate(
      duration,
      (linear) => {
        const t = linear < 0.5 ? 4 * linear ** 3 : 1 - (-2 * linear + 2) ** 3 / 2;
        const u = 1 - t;
        this.place({
          x: u * u * from.x + 2 * u * t * control.x + t * t * point.x,
          y: u * u * from.y + 2 * u * t * control.y + t * t * point.y,
        });
      },
      () => motion === this.motion,
    );
  }

  destroy(): void {
    this.element.remove();
  }

  private place(point: Point): void {
    this.position = point;
    this.placed = true;
    this.element.style.transition = "opacity 120ms linear";
    this.element.style.transform = `translate3d(${String(point.x - 3)}px, ${String(point.y - 2)}px, 0)`;
  }
}
