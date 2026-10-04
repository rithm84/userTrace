/**
 * The city's stepped loops (lit windows flickering, people swapping their two drawn frames, bar
 * charts on twos) are switched by the city's clock rather than by CSS animations: a CSS animation
 * gives every flickering window its own compositor layer, and dozens of them multiply the layers the
 * browser has to build each frame, while a switch written only when it flips costs a tiny repaint.
 *
 * A part is shown for `span` of every `period` milliseconds, starting `from` of the way into the
 * period, with the whole cycle advanced by `offset` milliseconds.
 */
export type Cycle = Readonly<{ period: number; offset: number; from: number; span: number }>;

export function shownAt(cycle: Cycle, ms: number): boolean {
  const phase = (ms + cycle.offset) / cycle.period - cycle.from;
  return phase - Math.floor(phase) < cycle.span;
}

/** The data attributes that mark a part as cycling, with its opacity at the loop's start. */
export function cycleAttributes(cycle: Cycle): Readonly<Record<string, string>> {
  return {
    'data-cycle': [cycle.period, cycle.offset, cycle.from, cycle.span].join(' '),
    opacity: shownAt(cycle, 0) ? '1' : '0',
  };
}

/** Reads a part's cycle back; a malformed one is a rendering bug, so it throws. */
export function readCycle(element: Element): Cycle {
  const values = (element.getAttribute('data-cycle') ?? '').split(' ').map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite) || values[0] <= 0) throw new Error(`A cycling part of the city has a malformed data-cycle: "${element.getAttribute('data-cycle')}".`);
  const [period, offset, from, span] = values;
  return { period, offset, from, span };
}
