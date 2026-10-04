/**
 * Reads the extension's closed shadow root from the real browser.
 *
 * A closed root is unreachable from page scripts, which is the point: the
 * product cannot touch the agent surface. The test therefore inspects it the
 * way a debugger does, over CDP with `pierce`, and drives it with real mouse
 * input at the coordinates the browser reports.
 */
import type { CDPSession, Page } from "playwright-core";

type CdpNode = Readonly<{
  nodeId: number;
  nodeName: string;
  attributes?: readonly string[];
  children?: readonly CdpNode[];
  shadowRoots?: readonly CdpNode[];
  contentDocument?: CdpNode;
}>;

export type Box = Readonly<{ x: number; y: number; width: number; height: number }>;

const TEST_ID_ATTRIBUTE = "data-utrace-testid";

export class ShadowInspector {
  private readonly session: CDPSession;

  private constructor(session: CDPSession) {
    this.session = session;
  }

  static async create(page: Page): Promise<ShadowInspector> {
    const session = await page.context().newCDPSession(page);
    await session.send("DOM.enable");
    await session.send("Runtime.enable");
    return new ShadowInspector(session);
  }

  async nodeIds(testId: string): Promise<readonly number[]> {
    const document = (await this.session.send("DOM.getDocument", {
      depth: -1,
      pierce: true,
    })) as { root: CdpNode };
    const found: number[] = [];
    collect(document.root, testId, found);
    return found;
  }

  async exists(testId: string): Promise<boolean> {
    return (await this.nodeIds(testId)).length > 0;
  }

  async count(testId: string): Promise<number> {
    return (await this.nodeIds(testId)).length;
  }

  private async requireNodeId(testId: string): Promise<number> {
    const ids = await this.nodeIds(testId);
    const first = ids[0];
    if (first === undefined) {
      throw new Error(`No element carries ${TEST_ID_ATTRIBUTE}="${testId}".`);
    }
    return first;
  }

  /** Reads one property or dataset entry from the element, in the page; a promise is awaited. */
  async read(testId: string, expression: string): Promise<string | null> {
    const nodeId = await this.requireNodeId(testId);
    const resolved = (await this.session.send("DOM.resolveNode", { nodeId })) as {
      object: { objectId: string };
    };
    const result = (await this.session.send("Runtime.callFunctionOn", {
      objectId: resolved.object.objectId,
      functionDeclaration: `async function () { const value = await (${expression}); return value === undefined || value === null ? null : String(value); }`,
      awaitPromise: true,
      returnByValue: true,
    })) as { result: { value: string | null } };
    return result.result.value;
  }

  /** The name Chrome's accessibility tree gives the element. */
  async accessibleName(testId: string): Promise<string> {
    const nodeId = await this.requireNodeId(testId);
    const tree = (await this.session.send("Accessibility.getPartialAXTree", {
      nodeId,
      fetchRelatives: false,
    })) as { nodes: readonly { name?: { value?: unknown } }[] };
    const name = tree.nodes[0]?.name?.value;
    if (typeof name !== "string") {
      throw new Error(`${testId} has no accessible name.`);
    }
    return name;
  }

  async box(testId: string): Promise<Box> {
    const nodeId = await this.requireNodeId(testId);
    const model = (await this.session.send("DOM.getBoxModel", { nodeId })) as {
      model: { content: readonly number[]; width: number; height: number };
    };
    const [x, y] = model.model.content;
    if (x === undefined || y === undefined) {
      throw new Error(`${testId} has no box model.`);
    }
    return { x, y, width: model.model.width, height: model.model.height };
  }

  async centre(testId: string): Promise<Readonly<{ x: number; y: number }>> {
    const box = await this.box(testId);
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  }
}

function collect(node: CdpNode, testId: string, found: number[]): void {
  const attributes = node.attributes ?? [];
  for (let index = 0; index < attributes.length; index += 2) {
    if (attributes[index] === TEST_ID_ATTRIBUTE && attributes[index + 1] === testId) {
      found.push(node.nodeId);
    }
  }
  for (const child of [
    ...(node.children ?? []),
    ...(node.shadowRoots ?? []),
    ...(node.contentDocument === undefined ? [] : [node.contentDocument]),
  ]) {
    collect(child, testId, found);
  }
}
