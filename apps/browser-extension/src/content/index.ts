/**
 * The agent surface in the bound preview document.
 *
 * The service worker injects this script only after the control plane has
 * bound the document, so an unbound tab never receives it. It owns the
 * extension's interface — the orb, its controls, the agent cursor, and visual
 * scenes — and holds no control-plane credential and no durable state.
 */
import { AgentCursor } from "./cursor";
import { AgentOrb } from "./orb";
import { OrbCharacter } from "./character";
import { BridgeError, PageBridge } from "./bridgeClient";
import { SceneRunner, type CommandExecution } from "./scene";
import { SURFACE_STYLES } from "./styles";
import { SurfaceHost, type SurfaceLayers } from "./host";
import { failureFields, logError } from "../shared/log";
import {
  CONTENT_PORT_NAME,
  parseWorkerMessage,
  type ControlsState,
  type SurfaceMessage,
  type WorkerMessage,
} from "../shared/portMessages";

const MOUNT_FLAG = "__utraceAgentSurface";
const RECONNECT_DELAY_MS = 500;
const MAX_PORT_RECONNECTS = 5;

type Mounted = Readonly<{
  host: SurfaceHost;
  layers: SurfaceLayers;
  orb: AgentOrb;
  character: OrbCharacter;
  cursor: AgentCursor;
  bridge: PageBridge;
  scenes: SceneRunner;
  sessionId: string;
}>;

class AgentSurface {
  private port: chrome.runtime.Port | null = null;
  private mounted: Mounted | null = null;
  /** The mount in progress: a command that arrives during it waits for it. */
  private mounting: Promise<void> | null = null;
  // The orb and the character exist before the bridge handshake and the Rive
  // load finish, so state that arrives during the mount still reaches them.
  private orb: AgentOrb | null = null;
  private character: OrbCharacter | null = null;
  private controls: ControlsState | null = null;
  private reconnects = 0;

  start(): void {
    this.connect();
  }

  private connect(): void {
    const port = chrome.runtime.connect({ name: CONTENT_PORT_NAME });
    this.port = port;
    port.onMessage.addListener((message: unknown) => {
      let parsed: WorkerMessage;
      try {
        parsed = parseWorkerMessage(message);
      } catch (error) {
        logError("utrace.extension.surface_message_rejected", failureFields(error));
        return;
      }
      void this.receive(parsed).catch((error: unknown) => {
        logError("utrace.extension.surface_message_failed", {
          kind: parsed.kind,
          ...failureFields(error),
        });
      });
    });
    port.onDisconnect.addListener(() => {
      this.port = null;
      this.markDisconnected();
      if (this.reconnects >= MAX_PORT_RECONNECTS) {
        return;
      }
      this.reconnects += 1;
      setTimeout(() => {
        this.connect();
      }, RECONNECT_DELAY_MS);
    });
  }

  private send(message: SurfaceMessage): void {
    this.port?.postMessage(message);
  }

  private async receive(message: WorkerMessage): Promise<void> {
    switch (message.kind) {
      case "init":
        this.mounting = this.mount(message);
        try {
          await this.mounting;
        } finally {
          this.mounting = null;
        }
        return;
      case "character_state":
        this.character?.apply(message.projection);
        return;
      case "controls_state":
        this.controls = message.controls;
        this.orb?.applyControls(message.controls);
        this.notifyPause(message.controls);
        return;
      case "command":
        await this.runCommand(message);
        return;
      case "withdraw_command":
        this.mounted?.scenes.withdraw(message.commandId);
        return;
      case "clear_scenes":
        this.mounted?.scenes.clear();
        return;
      case "orb_settings":
        this.orb?.applySettings(message.orb);
        return;
      case "teardown":
        this.teardown();
        return;
    }
  }

  private async mount(message: Extract<WorkerMessage, { kind: "init" }>): Promise<void> {
    if (this.mounted !== null) {
      this.character?.apply(message.projection);
      this.orb?.applyControls(message.controls);
      this.send({ kind: "surface_ready", nonce: message.nonce });
      return;
    }

    const host = new SurfaceHost({
      styles: SURFACE_STYLES,
      onRemovedPermanently: (reason: string) => {
        this.send({ kind: "readiness_report", report: "withdrawn", reason });
        this.teardown();
      },
    });
    const layers = host.mount();

    const orb = new AgentOrb(layers.character, message.orb, message.controls, {
      onControlAction: (action) => {
        this.send({ kind: "control_action", action });
      },
      onReturnToSession: () => {
        this.send({ kind: "return_to_session" });
      },
      onSettingsChanged: (orbSettings) => {
        this.send({ kind: "orb_settings", orb: orbSettings });
      },
    });

    this.orb = orb;
    const character = new OrbCharacter(orb.canvas, message.projection);
    this.character = character;

    let bridge: PageBridge;
    try {
      bridge = await PageBridge.attach(message.nonce);
      if (bridge.documentAttachmentId !== message.documentAttachmentId) {
        throw new Error(
          `The page bridge attached document ${bridge.documentAttachmentId}, not the bound ${message.documentAttachmentId}.`,
        );
      }
      await character.load();
    } catch (error) {
      host.teardown();
      this.orb = null;
      this.character = null;
      this.send({
        kind: "readiness_report",
        report: "withdrawn",
        reason: reasonOf(error),
      });
      throw error;
    }

    const cursor = new AgentCursor(layers.cursor);
    this.mounted = {
      host,
      layers,
      orb,
      character,
      cursor,
      bridge,
      scenes: new SceneRunner(layers, bridge, cursor),
      sessionId: message.sessionId,
    };
    this.controls = message.controls;
    this.send({ kind: "surface_ready", nonce: message.nonce });
  }

  private async runCommand(
    message: Extract<WorkerMessage, { kind: "command" }>,
  ): Promise<void> {
    // A freshly bound document shows the orb before its page bridge and character
    // finish loading; a gesture sent then plays once they have.
    await this.mounting?.catch(() => undefined);
    const mounted = this.mounted;
    if (mounted === null) {
      this.send({
        kind: "command_result",
        commandId: message.frame.payload.command_id,
        outcome: "failed",
        reason: "surface_not_mounted",
        observedTargets: [],
      });
      return;
    }

    let execution: CommandExecution;
    try {
      execution = await mounted.scenes.execute(message.frame);
    } catch (error) {
      // Report the failure now, with its cause, instead of leaving the command
      // pending until its deadline; the caller still logs the error.
      this.send({
        kind: "command_result",
        commandId: message.frame.payload.command_id,
        outcome: "failed",
        reason: executionFailureReason(error),
        observedTargets: [],
      });
      throw error;
    }
    this.send({
      kind: "command_result",
      commandId: message.frame.payload.command_id,
      outcome: execution.outcome,
      ...(execution.reason === undefined ? {} : { reason: execution.reason }),
      observedTargets: [...execution.observedTargets],
    });
  }

  /** An acknowledged pause clears the scene and quiets the shared runtime. */
  private notifyPause(controls: ControlsState): void {
    const mounted = this.mounted;
    if (mounted === null) {
      return;
    }
    if (controls.pause === "paused" || controls.pause === "ended") {
      mounted.scenes.clear();
    }
    mounted.bridge.notifyPause(
      mounted.sessionId,
      controls.pause,
      controls.sessionControlRevision,
    );
  }

  private markDisconnected(): void {
    const controls = this.controls;
    if (controls === null) {
      return;
    }
    const disconnected: ControlsState = { ...controls, connected: false };
    this.controls = disconnected;
    this.mounted?.orb.applyControls(disconnected);
  }

  private teardown(): void {
    const mounted = this.mounted;
    this.mounted = null;
    this.orb = null;
    this.character = null;
    if (mounted === null) {
      return;
    }
    mounted.scenes.clear();
    mounted.cursor.destroy();
    mounted.character.destroy();
    mounted.orb.destroy();
    mounted.bridge.close();
    mounted.host.teardown();
  }
}

/** The reason code a scene that threw reports: the error's own code when it has one. */
function executionFailureReason(error: unknown): string {
  if (error instanceof BridgeError) {
    return error.code;
  }
  return "scene_execution_failed";
}

function reasonOf(error: unknown): string {
  if (error !== null && typeof error === "object" && "code" in error) {
    const { code } = error;
    if (typeof code === "string") {
      return code;
    }
  }
  return "surface_mount_failed";
}

function main(): void {
  const scope = window as unknown as Record<string, unknown>;
  if (scope[MOUNT_FLAG] === true) {
    return;
  }
  scope[MOUNT_FLAG] = true;
  new AgentSurface().start();
}

main();
