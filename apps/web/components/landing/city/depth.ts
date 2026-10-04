import { screenBounds, type Box } from './iso';

const EPSILON = 1e-6;

/** True when `a` lies wholly on the far side of `b` along some axis, so `a` can never cover `b`. */
function behind(a: Box, b: Box): boolean {
  return a.x + a.w <= b.x + EPSILON || a.y + a.d <= b.y + EPSILON || a.z + a.h <= b.z + EPSILON;
}

function nearness(b: Box): number {
  return b.x + b.w / 2 + b.y + b.d / 2 + (b.z + b.h / 2) * 0.01;
}

export type DepthNode = Readonly<{ id: string; box: Box; rig: boolean }>;
export type Leveled<T> = Readonly<{ node: T; level: number }>;

/**
 * Orders nodes back to front and assigns each the lowest paint level it can take. Pairs that
 * overlap on screen are ordered by which lies on the far side of the other; pairs that do not
 * separate (a person inside a lot) fall back to the nearer centre; `chains` add explicit
 * before/after pairs (a vehicle's far half, its rider, its near half). A rig at level L is drawn
 * after plane L, so anything in front of it that overlaps it lands on a later plane, and
 * everything else stays on plane 0. A cycle is a layout error, so it throws with the ids involved.
 */
export function paintLevels<T extends DepthNode>(nodes: readonly T[], chains: readonly (readonly [string, string])[]): readonly Leveled<T>[] {
  const count = nodes.length;
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const bounds = nodes.map((node) => screenBounds(node.box));
  const after: number[][] = Array.from({ length: count }, () => []);
  const waiting = new Array<number>(count).fill(0);
  const link = (first: number, second: number): void => {
    after[first].push(second);
    waiting[second] += 1;
  };
  const chained = new Set(chains.map(([a, b]) => `${a}|${b}`));
  for (const [a, b] of chains) {
    const first = index.get(a);
    const second = index.get(b);
    if (first === undefined || second === undefined) throw new Error(`A paint chain names a node that is not in the city: ${a} -> ${b}.`);
    link(first, second);
  }
  for (let i = 0; i < count; i += 1) {
    for (let j = i + 1; j < count; j += 1) {
      if (chained.has(`${nodes[i].id}|${nodes[j].id}`) || chained.has(`${nodes[j].id}|${nodes[i].id}`)) continue;
      const a = bounds[i];
      const b = bounds[j];
      if (a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top) continue;
      const ab = behind(nodes[i].box, nodes[j].box);
      const ba = behind(nodes[j].box, nodes[i].box);
      const first = ab && !ba ? i : ba && !ab ? j : nearness(nodes[i].box) <= nearness(nodes[j].box) ? i : j;
      link(first, first === i ? j : i);
    }
  }
  const level = new Array<number>(count).fill(0);
  const order: Leveled<T>[] = [];
  const ready = nodes.map((_, i) => i).filter((i) => waiting[i] === 0);
  while (ready.length > 0) {
    ready.sort((a, b) => nearness(nodes[b].box) - nearness(nodes[a].box));
    const next = ready.pop();
    if (next === undefined) break;
    order.push({ node: nodes[next], level: level[next] });
    for (const k of after[next]) {
      level[k] = Math.max(level[k], level[next] + (nodes[next].rig ? 1 : 0));
      waiting[k] -= 1;
      if (waiting[k] === 0) ready.push(k);
    }
  }
  if (order.length !== count) {
    const stuck = nodes.filter((_, i) => waiting[i] > 0).map((node) => node.id);
    throw new Error(`The city plan has boxes that cannot be depth-sorted (a cycle among: ${stuck.join(', ')}).`);
  }
  return order;
}
