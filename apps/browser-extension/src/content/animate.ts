/**
 * Time-driven animation for the agent cursor and its marks.
 *
 * Elapsed time, not painted frames, ends an animation: animation frames only
 * paint its progress. A window that stops painting part-way, because it was
 * covered or minimised, therefore still finishes the gesture on time and reports
 * its result, rather than holding the command until its deadline.
 */

/**
 * Paints `paint(progress)` on each animation frame for `durationMs`, from 0 to
 * exactly 1, and resolves once that time has passed. It stops early, without
 * painting further, as soon as `current()` is false.
 */
export function animate(
  durationMs: number,
  paint: (progress: number) => void,
  current: () => boolean,
): Promise<void> {
  const started = performance.now();
  return new Promise((resolve) => {
    let finished = false;
    const finish = (): void => {
      if (finished) {
        return;
      }
      finished = true;
      if (current()) {
        paint(1);
      }
      resolve();
    };
    const frame = (now: number): void => {
      if (finished) {
        return;
      }
      if (!current()) {
        finished = true;
        resolve();
        return;
      }
      const progress = Math.min((now - started) / durationMs, 1);
      if (progress >= 1) {
        finish();
        return;
      }
      paint(progress);
      requestAnimationFrame(frame);
    };
    setTimeout(finish, durationMs);
    requestAnimationFrame(frame);
  });
}
