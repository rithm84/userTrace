/**
 * The extension's own top-layer host in the bound top document.
 *
 * One host element carries a closed shadow root with three separate layers —
 * character, cursor, and scene — so product and extension styles cannot reach
 * each other. Only the orb and its control pills accept pointer input; the
 * cursor and scene layers stay click-through, and the main-world bridge cannot
 * reach this tree at all.
 *
 * If the product removes the host once, the surface reinjects it cleanly.
 * Repeated removal withdraws readiness rather than fighting the page.
 */
export const HOST_TAG = "utrace-agent-host";
const MAX_REINJECTIONS = 1;

export type SurfaceLayers = Readonly<{
  host: HTMLElement;
  root: ShadowRoot;
  character: HTMLElement;
  cursor: HTMLElement;
  scene: SVGSVGElement;
}>;

export type HostOptions = Readonly<{
  styles: string;
  onRemovedPermanently: (reason: string) => void;
}>;

export class SurfaceHost {
  private readonly options: HostOptions;
  private layers: SurfaceLayers | null = null;
  private observer: MutationObserver | null = null;
  private reinjections = 0;
  private torndown = false;

  constructor(options: HostOptions) {
    this.options = options;
  }

  mount(): SurfaceLayers {
    const layers = this.createLayers();
    this.layers = layers;
    this.watch();
    return layers;
  }

  current(): SurfaceLayers {
    if (this.layers === null) {
      throw new Error("The uTrace agent surface is not mounted.");
    }
    return this.layers;
  }

  teardown(): void {
    this.torndown = true;
    this.observer?.disconnect();
    this.observer = null;
    this.layers?.host.remove();
    this.layers = null;
  }

  private createLayers(): SurfaceLayers {
    const host = document.createElement(HOST_TAG);
    for (const [property, value] of Object.entries({
      all: "initial",
      position: "fixed",
      inset: "0",
      width: "100%",
      height: "100%",
      "z-index": "2147483647",
      "pointer-events": "none",
      display: "block",
    })) {
      host.style.setProperty(property, value, "important");
    }

    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = this.options.styles;
    root.append(style);

    const scene = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    scene.setAttribute("class", "layer scene-layer");
    scene.setAttribute("data-utrace-testid", "scene-layer");
    scene.setAttribute("aria-hidden", "true");

    const cursor = document.createElement("div");
    cursor.className = "layer cursor-layer";
    cursor.dataset["utraceTestid"] = "cursor-layer";
    cursor.setAttribute("aria-hidden", "true");

    const character = document.createElement("div");
    character.className = "layer character-layer";
    character.dataset["utraceTestid"] = "character-layer";

    root.append(scene, cursor, character);
    document.documentElement.append(host);

    return { host, root, character, cursor, scene };
  }

  /**
   * Watches for the product removing the host. One clean reinjection is
   * allowed; a second removal is reported so the control plane can withdraw
   * readiness.
   */
  private watch(): void {
    this.observer?.disconnect();
    const observer = new MutationObserver(() => {
      if (this.torndown || this.layers === null) {
        return;
      }
      if (this.layers.host.isConnected) {
        return;
      }
      if (this.reinjections >= MAX_REINJECTIONS) {
        observer.disconnect();
        this.observer = null;
        this.options.onRemovedPermanently("interface_host_removed_repeatedly");
        return;
      }
      this.reinjections += 1;
      document.documentElement.append(this.layers.host);
    });
    observer.observe(document.documentElement, { childList: true, subtree: false });
    this.observer = observer;
  }
}
