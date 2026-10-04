/**
 * Structural taint.
 *
 * A node contained by a sensitive surface is tainted for the rest of its life.
 * Taint is recorded against node identity in a `WeakSet`, so moving the node out
 * of the sensitive surface cannot launder it into an allowed region. Taint
 * clears only when the node is destroyed and the entry becomes unreachable.
 */

export class TaintRegistry {
  readonly #tainted = new WeakSet<Node>();

  /** Marks `root` and its current subtree, including shadow roots the client registered. */
  taintSubtree(root: Node): void {
    this.#tainted.add(root);
    if (root instanceof Element || root instanceof DocumentFragment) {
      for (const descendant of root.querySelectorAll("*")) this.#tainted.add(descendant);
    }
  }

  taintNode(node: Node): void {
    this.#tainted.add(node);
  }

  /** True when the node itself carries taint or inherits it from a live ancestor. */
  isTainted(node: Node): boolean {
    let current: Node | null = node;
    while (current !== null) {
      if (this.#tainted.has(current)) return true;
      current = current.parentNode;
    }
    return false;
  }

  /** Propagates taint to nodes added beneath an already tainted parent. */
  onNodeAdded(node: Node): void {
    const parent = node.parentNode;
    if (parent !== null && this.isTainted(parent)) this.taintSubtree(node);
  }
}
