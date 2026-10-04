import type { Rive } from "@rive-app/webgl2";

/**
 * Sizes a rig's canvas to its on-screen box (measured through any CSS zoom) only when the
 * backing store is stale. Resizing clears the canvas until the next frame, which reads as a
 * blink, so it must not happen on every state change.
 */
export function fitRigCanvas(rive: Rive, host: HTMLElement | null, dpr: number): void {
  const canvas = host?.querySelector("canvas") ?? null;
  if (canvas === null) throw new Error("Cannot fit the rig canvas: the Rive component has not rendered its canvas yet.");
  const rect = canvas.getBoundingClientRect();
  if (canvas.width === Math.trunc(rect.width * dpr) && canvas.height === Math.trunc(rect.height * dpr)) return;
  rive.resizeDrawingSurfaceToCanvas(dpr);
}
