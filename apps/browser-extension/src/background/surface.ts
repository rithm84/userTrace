/**
 * Injection and ownership of the content surface for the bound tab.
 *
 * Nothing is injected declaratively: the main-world bridge and the content
 * script reach a document only after the control plane has bound it. The named
 * port is accepted only from the bound tab's top frame, and the init message
 * carries the handshake nonce that pairs the content script with the bridge it
 * was injected beside.
 */
import {
  CONTENT_PORT_NAME,
  parseSurfaceMessage,
  type ControlsState,
  type CharacterProjectionMessage,
  type OrbSettings,
  type SurfaceMessage,
  type WorkerMessage,
} from "../shared/portMessages";
import { BRIDGE_HANDSHAKE_GLOBAL } from "../shared/bridgeHandshake";

const BRIDGE_FILE = "main-world-bridge.js";
const CONTENT_FILE = "content.js";

export type SurfaceAttachment = Readonly<{
  nonce: string;
  sessionId: string;
  documentAttachmentId: string;
}>;

/**
 * Read when the surface connects rather than when it is injected, so a
 * character or control revision that arrives during the injection still
 * reaches the first frame the surface renders.
 */
export type SurfaceState = Readonly<{
  projection: CharacterProjectionMessage;
  orb: OrbSettings;
  controls: ControlsState;
}>;

export type SurfaceOptions = Readonly<{
  onMessage: (tabId: number, message: SurfaceMessage) => void;
  currentState: () => SurfaceState;
}>;

type Attachment = Readonly<{
  tabId: number;
  attachment: SurfaceAttachment;
  port: chrome.runtime.Port | null;
}>;

export class SurfaceController {
  private readonly options: SurfaceOptions;
  private attachment: Attachment | null = null;

  constructor(options: SurfaceOptions) {
    this.options = options;
  }

  get boundTabId(): number | null {
    return this.attachment?.tabId ?? null;
  }

  /** Injects the bridge and the surface into the freshly bound document. */
  async attach(tabId: number, attachment: SurfaceAttachment): Promise<void> {
    if (this.attachment !== null && this.attachment.tabId !== tabId) {
      this.detach(this.attachment.tabId, "superseded_by_tab");
    }
    this.attachment = { tabId, attachment, port: null };

    const target: chrome.scripting.InjectionTarget = { tabId, frameIds: [0] };
    await chrome.scripting.executeScript({
      target,
      world: "MAIN",
      injectImmediately: true,
      func: carryHandshakeNonce,
      args: [BRIDGE_HANDSHAKE_GLOBAL, attachment.nonce],
    });
    await chrome.scripting.executeScript({
      target,
      world: "MAIN",
      injectImmediately: true,
      files: [BRIDGE_FILE],
    });
    await chrome.scripting.executeScript({
      target,
      world: "ISOLATED",
      injectImmediately: true,
      files: [CONTENT_FILE],
    });
  }

  /** Accepts the named port only from the bound tab's top frame. */
  handlePort(port: chrome.runtime.Port): boolean {
    const current = this.attachment;
    const tabId = port.sender?.tab?.id;
    if (
      port.name !== CONTENT_PORT_NAME ||
      current === null ||
      tabId === undefined ||
      tabId !== current.tabId ||
      port.sender?.frameId !== 0
    ) {
      port.disconnect();
      return false;
    }

    this.attachment = { ...current, port };
    port.onMessage.addListener((message: unknown) => {
      this.options.onMessage(tabId, parseSurfaceMessage(message));
    });
    port.onDisconnect.addListener(() => {
      if (this.attachment?.port === port) {
        this.attachment = { ...this.attachment, port: null };
      }
    });

    const state = this.options.currentState();
    port.postMessage({
      kind: "init",
      nonce: current.attachment.nonce,
      sessionId: current.attachment.sessionId,
      documentAttachmentId: current.attachment.documentAttachmentId,
      projection: state.projection,
      orb: state.orb,
      controls: state.controls,
    } satisfies WorkerMessage);
    return true;
  }

  /** Posts to the bound surface and says whether one was connected to receive it. */
  send(message: WorkerMessage): boolean {
    const port = this.attachment?.port ?? null;
    if (port === null) {
      return false;
    }
    port.postMessage(message);
    return true;
  }

  detach(tabId: number, reason: string): void {
    const current = this.attachment;
    if (current === null || current.tabId !== tabId) {
      return;
    }
    current.port?.postMessage({ kind: "teardown", reason } satisfies WorkerMessage);
    current.port?.disconnect();
    this.attachment = null;
  }
}

/**
 * Runs in the page's main world immediately before the bridge file, which
 * reads the nonce and removes it. It is serialised by `chrome.scripting`, so
 * it closes over nothing.
 */
function carryHandshakeNonce(property: string, nonce: string): void {
  Object.defineProperty(window, property, {
    value: nonce,
    configurable: true,
    enumerable: false,
    writable: true,
  });
}
