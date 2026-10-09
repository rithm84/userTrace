/**
 * The service worker's session coordinator.
 *
 * It owns the binding, the control connection, command routing, and the
 * surface for the bound tab. It never owns durable workflow truth: readiness,
 * character state, binding identity, and control outcomes are the control
 * plane's revisioned records, and everything held here is a short-lived
 * checkpoint that survives a service-worker suspension.
 */
import {
  IDLE_BINDING,
  activeSessionId,
  boundTabId,
  reduceBinding,
  shouldProbeFocusedTab,
  type BindingEffect,
  type BindingEvent,
  type BindingSnapshot,
} from "./bindingState";
import {
  EMPTY_COMMAND_REGISTRY,
  reduceCommands,
  type CommandContext,
  type CommandEffect,
  type CommandEvent,
  type CommandRegistry,
} from "./commandState";
import { ControlSocket, type ConnectionStatus } from "./controlSocket";
import { createChallenge, loadDeviceKeyPair, provePossession } from "./deviceKey";
import { forgetInstallation } from "./installation";
import { probeTabForPageProof } from "./probe";
import { supersedes } from "./revisions";
import {
  readCheckpoint,
  readInstallation,
  readOrbSettings,
  readPreferences,
  writeCheckpoint,
  writeOrbSettings,
  writePreferences,
  type InstallationRecord,
  type PreferencesCache,
} from "./storage";
import { SurfaceController } from "./surface";
import { PROTOCOL_VERSION, SCHEMA_VERSION, type ControlAction } from "../protocol/common";
import type {
  BindingOffer,
  CharacterStateFrame,
  InboundFrame,
  OutboundFrame,
  ReadinessFrame,
} from "../protocol/control";
import type { PreferencesUpdate } from "../shared/extensionMessages";
import { API_AUDIENCE } from "../shared/buildConfig";
import type { PreflightResultMessage } from "../shared/sessionTabMessages";
import type { ProofOfPossession } from "../protocol/installation";
import { newUuidV7, nowTimestamp } from "../shared/identifiers";
import { logWarning } from "../shared/log";
import {
  type CharacterProjectionMessage,
  type ControlsState,
  type OrbSettings,
  type SurfaceMessage,
} from "../shared/portMessages";

const DEADLINE_SWEEP_MS = 1_000;
const LOADED_TAB_PROBE_ATTEMPTS = 10;
const LOADED_TAB_PROBE_INTERVAL_MS = 500;

/**
 * One opaque string the page forwards verbatim. Encoding it keeps the proof's
 * structure out of the page's reach while leaving the control plane an exact
 * JSON document to verify.
 */
function encodeProof(proof: ProofOfPossession): string {
  const json = JSON.stringify(proof);
  let binary = "";
  for (const byte of new TextEncoder().encode(json)) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

const INITIAL_PROJECTION: CharacterProjectionMessage = Object.freeze({
  revision: 0,
  state: "idle",
  gazeX: 0,
  gazeY: 0,
  speechEnergy: 0,
  motionScale: 1,
});

const INITIAL_CONTROLS: ControlsState = Object.freeze({
  sessionControlRevision: 0,
  mute: "unmuted",
  pause: "running",
  connected: false,
  visualGuidanceEnabled: true,
  pendingAction: null,
});

export type OfferAnswer = Readonly<{ accepted: boolean; reason?: string }>;

export type StatusSnapshot = Readonly<{
  installation: InstallationRecord | null;
  connection: ConnectionStatus;
  connectionReason: string | null;
  bindingStatus: BindingSnapshot["status"];
  sessionId: string | null;
  readiness: ReadinessFrame["payload"] | null;
  preferences: PreferencesCache;
  orb: OrbSettings;
  protocolVersion: string;
}>;

export class SessionCoordinator {
  private readonly surface: SurfaceController;
  private installation: InstallationRecord | null = null;
  private socket: ControlSocket | null = null;
  private binding: BindingSnapshot = IDLE_BINDING;
  private commands: CommandRegistry = EMPTY_COMMAND_REGISTRY;
  private projection: CharacterProjectionMessage = INITIAL_PROJECTION;
  /** The session the character projection belongs to; revisions are per session. */
  private projectionSessionId: string | null = null;
  private controls: ControlsState = INITIAL_CONTROLS;
  /** The session the controls belong to; their revisions are per session. */
  private controlsSessionId: string | null = null;
  private readiness: ReadinessFrame["payload"] | null = null;
  private preferences: PreferencesCache;
  private orb: OrbSettings;
  private acknowledged = 0;
  private sessionTabId: number | null = null;
  private connection: ConnectionStatus = "idle";
  private connectionReason: string | null = null;
  private sweep: ReturnType<typeof setInterval> | null = null;

  private constructor(
    installation: InstallationRecord | null,
    preferences: PreferencesCache,
    orb: OrbSettings,
    acknowledged: number,
  ) {
    this.installation = installation;
    this.preferences = preferences;
    this.orb = orb;
    this.acknowledged = acknowledged;
    this.controls = { ...INITIAL_CONTROLS, visualGuidanceEnabled: preferences.visual_guidance_enabled };
    this.surface = new SurfaceController({
      onMessage: (tabId, message) => {
        void this.handleSurfaceMessage(tabId, message);
      },
      currentState: () => ({
        projection: this.projection,
        orb: this.orb,
        controls: this.controls,
      }),
    });
  }

  static async create(): Promise<SessionCoordinator> {
    const [installation, preferences, orb, checkpoint] = await Promise.all([
      readInstallation(),
      readPreferences(),
      readOrbSettings(),
      readCheckpoint(),
    ]);
    return new SessionCoordinator(
      installation,
      preferences,
      orb,
      checkpoint.acknowledged_sequence,
    );
  }

  status(): StatusSnapshot {
    return {
      installation: this.installation,
      connection: this.connection,
      connectionReason: this.connectionReason,
      bindingStatus: this.binding.status,
      sessionId: activeSessionId(this.binding),
      readiness: this.readiness,
      preferences: this.preferences,
      orb: this.orb,
      protocolVersion: PROTOCOL_VERSION,
    };
  }

  setInstallation(installation: InstallationRecord | null): void {
    if (installation === null) {
      this.withdrawInstallation("installation_revoked");
      return;
    }
    this.installation = installation;
  }

  /**
   * Withdraws the installation's authority. A later link is a new installation
   * whose control stream starts again from sequence zero.
   */
  private withdrawInstallation(reason: string): void {
    this.installation = null;
    this.acknowledged = 0;
    this.socket?.disconnect(reason);
    this.socket = null;
    void this.applyBinding({ type: "authority_lost", reason });
  }

  /**
   * The control plane refused this installation as unknown, as after a reset,
   * or revoked. The local record is stale, so it is dropped with its device key
   * and the browser can be linked again.
   */
  private async dropRefusedInstallation(reason: string): Promise<void> {
    console.warn("utrace.extension.installation_refused", { reason });
    this.withdrawInstallation(reason);
    await forgetInstallation();
  }

  /**
   * Answers the session tab's participant compatibility preflight.
   *
   * It reports only what this installation can prove: account-link state, the
   * required host permission, and a fresh proof of possession of the
   * registered device key. The control plane decides whether the participant
   * may enter the Interview Session; this answer is evidence, not a verdict.
   */
  async preflight(): Promise<PreflightResultMessage> {
    const installation = this.installation;
    const hostPermission = await chrome.permissions
      .contains({ origins: ["https://*/*", "http://*/*"] })
      .catch(() => false);
    if (installation === null || installation.account_link_state !== "linked") {
      return {
        type: "utrace_preflight_result",
        account_linked: false,
        host_permission: hostPermission,
        installation_proof: null,
        protocol_version: PROTOCOL_VERSION,
      };
    }
    // Connecting is the preflight's control-plane connectivity check, and the
    // only way an installation the control plane refuses is dropped before a
    // binding offer, which the control plane mints only after a passed preflight.
    this.socket ??= this.createSocket(installation);
    await this.socket.connect();
    const pair = await loadDeviceKeyPair();
    const proof = await provePossession(
      pair,
      createChallenge(API_AUDIENCE, "session_preflight", installation.installation_id),
    );
    return {
      type: "utrace_preflight_result",
      account_linked: true,
      host_permission: hostPermission,
      installation_proof: encodeProof(proof),
      protocol_version: PROTOCOL_VERSION,
    };
  }

  /** Accepts a binding offer that arrived from a uTrace session tab. */
  async receiveOffer(offer: BindingOffer, sessionTabId: number | null): Promise<OfferAnswer> {
    const installation = this.installation;
    if (installation === null) {
      return { accepted: false, reason: "installation_not_linked" };
    }
    if (installation.account_link_state !== "linked") {
      return { accepted: false, reason: "installation_revoked" };
    }

    this.sessionTabId = sessionTabId;
    await this.ensureSocket(installation);

    const answers = await this.applyBinding({
      type: "offer_received",
      offer,
      installationId: installation.installation_id,
      receivedAt: Date.now(),
    });
    return answers;
  }

  /** Probes a focused tab, but only while a session is waiting to bind. */
  async focusTab(tabId: number): Promise<void> {
    await this.observePageProof(tabId);
  }

  /**
   * Probes a focused tab that just finished loading. Opening the preview focuses
   * its tab while the page still loads, so the embedded SDK may answer only once
   * its gate has activated; the probe is retried briefly rather than waiting for
   * the participant to switch tabs.
   */
  async probeLoadedTab(tabId: number): Promise<void> {
    for (let attempt = 0; attempt < LOADED_TAB_PROBE_ATTEMPTS; attempt += 1) {
      if (!shouldProbeFocusedTab(this.binding) || (await this.observePageProof(tabId))) {
        return;
      }
      await new Promise((done) => setTimeout(done, LOADED_TAB_PROBE_INTERVAL_MS));
    }
  }

  /** Asks one tab for its page proof and applies it; `false` when nothing answered. */
  private async observePageProof(tabId: number): Promise<boolean> {
    if (!shouldProbeFocusedTab(this.binding)) {
      return false;
    }
    const probe = await probeTabForPageProof(tabId).catch(() => null);
    if (probe === null) {
      return false;
    }
    await this.applyBinding({
      type: "page_proof_observed",
      tabId,
      documentOrigin: probe.documentOrigin,
      proof: probe.response.payload.page_proof,
      claims: probe.claims,
      documentAttachmentId: probe.response.payload.document_attachment_id,
    });
    return true;
  }

  async closeTab(tabId: number): Promise<void> {
    if (tabId === this.sessionTabId) {
      this.sessionTabId = null;
    }
    await this.applyBinding({ type: "tab_closed", tabId });
  }

  connectSurface(port: chrome.runtime.Port): void {
    this.surface.handlePort(port);
  }

  /** Orb position and size are device-local, so this applies immediately. */
  async updateOrbSettings(orb: OrbSettings): Promise<void> {
    this.orb = orb;
    await writeOrbSettings(orb);
    this.surface.send({ kind: "orb_settings", orb });
  }

  /**
   * Global action preferences are control-plane owned, so a change is a
   * request. Without the connection it fails explicitly rather than pretending
   * a local cache is authoritative.
   */
  updatePreferences(preferences: PreferencesUpdate): Promise<void> {
    if (this.socket === null || this.connection !== "open") {
      return Promise.reject(
        new Error(
          "Global action preferences are owned by the control plane and cannot be changed while the extension is disconnected.",
        ),
      );
    }
    this.sendFrame({
      schema_version: SCHEMA_VERSION,
      kind: "preferences_update",
      message_id: newUuidV7(),
      sent_at: nowTimestamp(),
      payload: preferences,
    });
    return Promise.resolve();
  }

  private async ensureSocket(installation: InstallationRecord): Promise<void> {
    this.socket ??= this.createSocket(installation);
    this.socket.setSessionNeeded(true);
    await this.socket.connect();
    this.startSweep();
  }

  private createSocket(installation: InstallationRecord): ControlSocket {
    return new ControlSocket({
      installationId: installation.installation_id,
      acknowledgedSequence: this.acknowledged,
      pendingCommandIds: () => [...this.commands.pending.keys()],
      sessionId: () => activeSessionId(this.binding),
      onFrame: (frame) => {
        void this.handleFrame(frame);
      },
      onStatusChange: (status, reason) => {
        this.connection = status;
        this.connectionReason = reason;
        this.updateControls({ connected: status === "open" });
      },
      onSequenceAdvance: (sequence) => {
        this.acknowledged = sequence;
        void this.checkpoint();
      },
      onInstallationRefused: (reason) => {
        void this.dropRefusedInstallation(reason);
      },
    });
  }

  private startSweep(): void {
    if (this.sweep !== null) {
      return;
    }
    this.sweep = setInterval(() => {
      void this.applyCommands({ type: "deadline_check", now: Date.now() });
    }, DEADLINE_SWEEP_MS);
  }

  private async handleFrame(frame: InboundFrame): Promise<void> {
    switch (frame.kind) {
      case "binding_state":
        await this.applyBinding({ type: "binding_state_received", frame });
        return;
      case "readiness":
        if (
          !supersedes(
            this.readiness === null
              ? null
              : {
                  sessionId: this.readiness.session_id,
                  revision: this.readiness.readiness_revision,
                },
            { sessionId: frame.payload.session_id, revision: frame.payload.readiness_revision },
          )
        ) {
          return;
        }
        this.readiness = frame.payload;
        if (frame.payload.combined_readiness === "blocked") {
          await this.applyCommands({
            type: "authority_withdrawn",
            reason: "combined_readiness_blocked",
            now: Date.now(),
          });
        }
        return;
      case "character_state":
        this.applyCharacterState(frame);
        return;
      case "visual_command":
        await this.focusPreviewTab();
        await this.applyCommands({
          type: "command_received",
          frame,
          context: await this.commandContext(),
        });
        return;
      case "cancel":
        await this.applyCommands({
          type: "cancel_received",
          commandId: frame.payload.command_id,
          reason: frame.payload.reason,
          now: Date.now(),
        });
        return;
      case "control_action_result":
        this.updateControls({ pendingAction: null });
        await this.applySessionControls(frame.payload);
        return;
      case "controls_state":
        await this.applySessionControls(frame.payload);
        return;
      case "recovery_state":
        if (frame.payload.disposition === "restarted") {
          this.acknowledged = 0;
          await this.checkpoint();
        }
        return;
      case "preferences_state":
        this.preferences = {
          preferences_revision: frame.payload.preferences_revision,
          visual_guidance_enabled: frame.payload.visual_guidance_enabled,
          agent_cursor_enabled: frame.payload.agent_cursor_enabled,
        };
        await writePreferences(this.preferences);
        this.updateControls({ visualGuidanceEnabled: frame.payload.visual_guidance_enabled });
        return;
      case "heartbeat":
        return;
    }
  }

  private applyCharacterState(frame: CharacterStateFrame): void {
    if (
      !supersedes(
        this.projectionSessionId === null
          ? null
          : { sessionId: this.projectionSessionId, revision: this.projection.revision },
        { sessionId: frame.payload.session_id, revision: frame.payload.character_revision },
      )
    ) {
      return;
    }
    this.projectionSessionId = frame.payload.session_id;
    this.projection = {
      revision: frame.payload.character_revision,
      state: frame.payload.state,
      gazeX: frame.payload.gaze_x,
      gazeY: frame.payload.gaze_y,
      speechEnergy: frame.payload.speech_energy,
      motionScale: frame.payload.motion_scale,
    };
    this.surface.send({ kind: "character_state", projection: this.projection });
  }

  /**
   * Show one session's mute and pause state. Within a session an older revision
   * loses; a different session's state replaces whatever the pills showed, so
   * nothing a previous interview left behind carries into the next.
   */
  private async applySessionControls(
    payload: Extract<InboundFrame, { kind: "control_action_result" | "controls_state" }>["payload"],
  ): Promise<void> {
    if (
      payload.session_id === this.controlsSessionId &&
      payload.session_control_revision < this.controls.sessionControlRevision
    ) {
      return;
    }
    this.controlsSessionId = payload.session_id;
    this.updateControls({
      sessionControlRevision: payload.session_control_revision,
      mute: payload.mute_state,
      pause: payload.pause_state,
    });

    if (payload.pause_state === "paused" || payload.pause_state === "ended") {
      await this.applyCommands({
        type: "authority_withdrawn",
        reason: payload.pause_state === "paused" ? "session_paused" : "session_ended",
        now: Date.now(),
      });
    }
  }

  private async handleSurfaceMessage(tabId: number, message: SurfaceMessage): Promise<void> {
    switch (message.kind) {
      case "surface_ready":
        return;
      case "command_result":
        await this.applyCommands({
          type: "surface_result",
          commandId: message.commandId,
          outcome: message.outcome,
          ...(message.reason === undefined ? {} : { reason: message.reason }),
          observedTargets: message.observedTargets,
          now: Date.now(),
        });
        return;
      case "control_action":
        this.requestControlAction(message.action);
        return;
      case "return_to_session":
        await this.focusSessionTab();
        return;
      case "readiness_report":
        this.sendFrame({
          schema_version: SCHEMA_VERSION,
          kind: "readiness_report",
          message_id: newUuidV7(),
          sent_at: nowTimestamp(),
          payload: {
            session_id: activeSessionId(this.binding) ?? "",
            report: message.report,
            reason: message.reason,
            observed_at: nowTimestamp(),
          },
        });
        if (message.report === "withdrawn") {
          this.surface.detach(tabId, "readiness_withdrawn");
        }
        return;
      case "orb_settings":
        this.orb = message.orb;
        await writeOrbSettings(message.orb);
        return;
    }
  }

  /**
   * Mute, Pause, and End go to the control plane; a duplicate click reuses the
   * pending action rather than sending a second request.
   */
  private requestControlAction(action: ControlAction): void {
    if (this.controls.pendingAction !== null) {
      return;
    }
    const sessionId = activeSessionId(this.binding);
    if (sessionId === null) {
      return;
    }
    this.updateControls({ pendingAction: action });
    try {
      this.sendFrame({
        schema_version: SCHEMA_VERSION,
        kind: "control_action",
        message_id: newUuidV7(),
        sent_at: nowTimestamp(),
        payload: {
          action_id: newUuidV7(),
          session_id: sessionId,
          action,
          expected_session_control_revision: this.controls.sessionControlRevision,
          requested_at: nowTimestamp(),
          locally_confirmed: action === "end",
        },
      });
    } catch (error) {
      this.updateControls({ pendingAction: null, connected: false });
      throw error;
    }
  }

  /**
   * Brings the bound preview to the front before a gesture plays.
   *
   * The agent gestures while it talks, often while the customer is looking at the
   * session tab. A preview tab that is not in front is never seen, and a window
   * hidden behind another pauses the animation frames the cursor moves on, so the
   * gesture is shown where the customer is looking only once its tab and window
   * are brought forward.
   */
  private async focusPreviewTab(): Promise<void> {
    const tabId = boundTabId(this.binding);
    if (tabId === null) {
      return;
    }
    const tab = await chrome.tabs.update(tabId, { active: true });
    if (tab?.windowId !== undefined) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  }

  /** Return to session is browser-local and works without the connection. */
  private async focusSessionTab(): Promise<void> {
    const sessionTabId = this.sessionTabId;
    if (sessionTabId === null) {
      return;
    }
    const tab = await chrome.tabs.update(sessionTabId, { active: true });
    if (tab?.windowId !== undefined) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  }

  private async commandContext(): Promise<CommandContext> {
    const tabId = boundTabId(this.binding);
    return {
      extensionBindingId:
        this.binding.status === "bound" ? this.binding.extensionBindingId : null,
      documentAttachmentId:
        this.binding.status === "bound" ? this.binding.documentAttachmentId : null,
      tabActive: tabId === null ? false : await isTabActive(tabId),
      combinedReady: this.readiness?.combined_readiness === "ready",
      paused: this.controls.pause !== "running",
      visualGuidanceEnabled: this.preferences.visual_guidance_enabled,
      now: Date.now(),
    };
  }

  private async applyBinding(event: BindingEvent): Promise<OfferAnswer> {
    const transition = reduceBinding(this.binding, event, PROTOCOL_VERSION);
    this.binding = transition.state;
    let answer: OfferAnswer = { accepted: true };

    for (const effect of transition.effects) {
      answer = (await this.applyBindingEffect(effect)) ?? answer;
    }
    await this.checkpoint();
    return answer;
  }

  private async applyBindingEffect(effect: BindingEffect): Promise<OfferAnswer | null> {
    switch (effect.type) {
      case "answer_offer":
        return effect.accepted
          ? { accepted: true }
          : { accepted: false, reason: effect.reason };
      case "submit_binding":
        this.sendFrame({
          schema_version: SCHEMA_VERSION,
          kind: "binding_offer",
          message_id: newUuidV7(),
          sent_at: nowTimestamp(),
          payload: {
            request: effect.request,
            page_proof: effect.proof,
            tab_locator: { tab_id: effect.tabId, frame_id: 0 },
            document_origin: effect.documentOrigin,
          },
        });
        return null;
      case "attach_surface": {
        const sessionId = activeSessionId(this.binding);
        if (sessionId === null) {
          return null;
        }
        await this.surface.attach(effect.tabId, {
          nonce: newUuidV7(),
          sessionId,
          documentAttachmentId: effect.documentAttachmentId,
        });
        return null;
      }
      case "detach_surface":
        this.surface.detach(effect.tabId, effect.reason);
        await this.applyCommands({
          type: "authority_withdrawn",
          reason: effect.reason,
          now: Date.now(),
        });
        return null;
      case "reload_tab":
        await chrome.tabs.reload(effect.tabId);
        return null;
    }
  }

  private async applyCommands(event: CommandEvent): Promise<void> {
    const transition = reduceCommands(this.commands, event);
    this.commands = transition.registry;
    for (const effect of transition.effects) {
      this.applyCommandEffect(effect);
    }
    await this.checkpoint();
  }

  private applyCommandEffect(effect: CommandEffect): void {
    switch (effect.type) {
      case "route_command":
        // A gesture with no surface to play it fails now rather than at its deadline.
        if (!this.surface.send({ kind: "command", frame: effect.frame })) {
          void this.applyCommands({
            type: "surface_result",
            commandId: effect.frame.payload.command_id,
            outcome: "failed",
            reason: "surface_not_connected",
            observedTargets: [],
            now: Date.now(),
          });
        }
        return;
      case "withdraw_command":
        this.surface.send({
          kind: "withdraw_command",
          commandId: effect.commandId,
          reason: effect.reason,
        });
        return;
      case "clear_scenes":
        this.surface.send({ kind: "clear_scenes", reason: effect.reason });
        return;
      case "emit_result":
        this.sendFrame({
          schema_version: SCHEMA_VERSION,
          kind: "command_result",
          message_id: newUuidV7(),
          sent_at: nowTimestamp(),
          payload: {
            command_id: effect.result.command_id,
            outcome: effect.result.outcome,
            ...(effect.result.reason === undefined ? {} : { reason: effect.result.reason }),
            extension_binding_id: effect.result.extension_binding_id,
            document_attachment_id: effect.result.document_attachment_id,
            observed_targets: [...effect.result.observed_targets],
            completed_at: effect.result.completed_at,
          },
        });
        return;
    }
  }

  private updateControls(changes: Partial<ControlsState>): void {
    this.controls = { ...this.controls, ...changes };
    this.surface.send({ kind: "controls_state", controls: this.controls });
  }

  private sendFrame(frame: OutboundFrame): void {
    const socket = this.socket;
    if (socket === null) {
      logWarning("utrace.extension.frame_dropped", { kind: frame.kind, reason: "no_socket" });
      return;
    }
    socket.send(frame);
  }

  private async checkpoint(): Promise<void> {
    const sessionId = activeSessionId(this.binding);
    const tabId = boundTabId(this.binding);
    await writeCheckpoint({
      acknowledged_sequence: this.acknowledged,
      ...(sessionId === null ? {} : { session_id: sessionId }),
      ...(this.binding.status === "bound"
        ? {
            extension_binding_id: this.binding.extensionBindingId,
            document_attachment_id: this.binding.documentAttachmentId,
          }
        : {}),
      ...(tabId === null ? {} : { tab_id: tabId }),
      pending_command_ids: [...this.commands.pending.keys()],
    });
  }
}

/**
 * The bound tab must be the active tab of its window. Whether that window also
 * holds operating-system input focus is not visibility, so it is not checked
 * here; the content surface additionally refuses to execute while
 * `document.visibilityState` is not `visible`.
 */
async function isTabActive(tabId: number): Promise<boolean> {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  return tab?.active === true;
}
