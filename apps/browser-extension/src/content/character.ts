/**
 * The packaged Rive character inside the orb.
 *
 * Both the session tab and the extension render the same artboard and state
 * machine and ignore older revisions, and neither infers durable session state
 * from local animation. The runtime, its WebAssembly, and the character file
 * are all packaged: `RuntimeLoader`'s CDN fallback is disabled so nothing is
 * fetched from the network.
 *
 * The canvas backing store always equals the canvas's painted size in device
 * pixels. A `device-pixel-content-box` observation reports that size exactly,
 * so the store follows the orb's diameter, a device-pixel-ratio change, and
 * browser zoom, and is re-sized only when stale: re-sizing clears the canvas
 * until the next frame.
 */
import { Rive, RuntimeLoader } from "@rive-app/canvas";
import {
  acceptCharacterProjection,
  type AcceptedCharacterProjection,
} from "@utrace/agent-character/contracts";

import type { CharacterProjectionMessage } from "../shared/portMessages";

const ARTBOARD = "AgentCharacter";
const STATE_MACHINE = "AgentCharacterMachine";
const CHARACTER_FILE = "agent-character.riv";
const WASM_FILE = "rive.wasm";

export type CharacterErrorCode = "character_asset_failed" | "character_binding_mismatch";

export class CharacterError extends Error {
  readonly code: CharacterErrorCode;

  constructor(code: CharacterErrorCode, message: string) {
    super(message);
    this.name = "CharacterError";
    this.code = code;
  }
}

let runtimeConfigured = false;

function configureRuntime(): void {
  if (runtimeConfigured) {
    return;
  }
  RuntimeLoader.setWasmFallbackUrl(null);
  RuntimeLoader.setWasmUrl(chrome.runtime.getURL(WASM_FILE));
  runtimeConfigured = true;
}

export class OrbCharacter {
  private readonly canvas: HTMLCanvasElement;
  private readonly observer: ResizeObserver;
  private rive: Rive | null = null;
  private pixelRatio = window.devicePixelRatio;
  private applied: AcceptedCharacterProjection | null = null;
  private latest: CharacterProjectionMessage;

  constructor(canvas: HTMLCanvasElement, projection: CharacterProjectionMessage) {
    this.canvas = canvas;
    this.latest = projection;
    this.observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        this.fitBackingStore(entry);
      }
    });
    this.observer.observe(canvas, { box: "device-pixel-content-box" });
  }

  async load(): Promise<void> {
    configureRuntime();

    const response = await fetch(chrome.runtime.getURL(CHARACTER_FILE));
    if (!response.ok) {
      throw new CharacterError(
        "character_asset_failed",
        `The packaged agent character could not be read: ${String(response.status)}.`,
      );
    }
    const buffer = await response.arrayBuffer();

    await new Promise<void>((resolve, reject) => {
      const rive = new Rive({
        canvas: this.canvas,
        buffer,
        artboard: ARTBOARD,
        stateMachines: STATE_MACHINE,
        autoplay: true,
        autoBind: true,
        enableRiveAssetCDN: false,
        onLoad: () => {
          this.redraw(rive);
          resolve();
        },
        onLoadError: () => {
          reject(
            new CharacterError(
              "character_asset_failed",
              "The Rive runtime could not load the packaged agent character.",
            ),
          );
        },
      });
      this.rive = rive;
    });

    this.apply(this.latest);
  }

  /** Applies a projection, ignoring revisions older than the applied one. */
  apply(projection: CharacterProjectionMessage): void {
    this.latest = projection;
    const rive = this.rive;
    if (rive === null) {
      return;
    }
    const decision = acceptCharacterProjection(this.applied, projection);
    if (decision.kind !== "applied") {
      return;
    }

    const instance = rive.viewModelInstance;
    if (instance === null) {
      throw new CharacterError(
        "character_binding_mismatch",
        "The packaged character exposes no auto-bound view model instance.",
      );
    }

    const state = instance.enum("state");
    const gazeX = instance.number("gazeX");
    const gazeY = instance.number("gazeY");
    const speechEnergy = instance.number("speechEnergy");
    const motionScale = instance.number("motionScale");
    if (
      state === null ||
      gazeX === null ||
      gazeY === null ||
      speechEnergy === null ||
      motionScale === null
    ) {
      throw new CharacterError(
        "character_binding_mismatch",
        "The packaged character is missing the state, gaze, speech, or motion bindings.",
      );
    }

    gazeX.value = decision.projection.gazeX;
    gazeY.value = decision.projection.gazeY;
    speechEnergy.value = decision.projection.speechEnergy;
    motionScale.value = decision.projection.motionScale;
    state.value = decision.projection.state;
    this.applied = decision.projection;
    this.canvas.dataset["utraceState"] = decision.projection.state;
    this.canvas.dataset["utraceRevision"] = String(decision.projection.revision);
  }

  destroy(): void {
    this.observer.disconnect();
    this.rive?.cleanup();
    this.rive = null;
  }

  private fitBackingStore(entry: ResizeObserverEntry): void {
    const device = entry.devicePixelContentBoxSize[0];
    const css = entry.contentBoxSize[0];
    if (device === undefined || css === undefined) {
      throw new Error("The orb canvas observation carries no device-pixel or CSS content box.");
    }
    // A canvas with no painted area has nothing to fit; Rive pauses drawing it.
    if (css.inlineSize === 0 || css.blockSize === 0) {
      return;
    }
    this.pixelRatio = device.inlineSize / css.inlineSize;
    if (this.canvas.width === device.inlineSize && this.canvas.height === device.blockSize) {
      return;
    }
    this.canvas.width = device.inlineSize;
    this.canvas.height = device.blockSize;
    if (this.rive !== null) {
      this.redraw(this.rive);
    }
  }

  /** Lays the artboard out on the current backing store and paints a frame. */
  private redraw(rive: Rive): void {
    rive.devicePixelRatioUsed = this.pixelRatio;
    rive.resizeToCanvas();
    rive.drawFrame();
  }
}
