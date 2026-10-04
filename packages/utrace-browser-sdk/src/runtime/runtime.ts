/**
 * The top-document observation runtime.
 *
 * One runtime exists per document. The top-document runtime is the coordinator
 * and the only browser telemetry exporter for the page: it owns the export
 * buffer, the ingestion WebSocket, readiness, the child-frame coordinator, and
 * the dedicated-worker capability grants. The observation engine itself lives
 * in `ObservationCore`, which an instrumented child frame shares.
 *
 * The embedded SDK is the only component allowed to create this runtime; the
 * extension joins it through the bridge and never creates a competing one.
 */

import type { BrowserSdkConfig } from "../core/config.js";
import type { BrowserView } from "../core/dom.js";
import type { RuntimeFailureCode } from "../core/failure.js";
import { ActivationError, ExportError, UTraceRuntimeError } from "../core/failure.js";
import type { RandomSource } from "../core/ids.js";
import { uuidv7, webCryptoRandom } from "../core/ids.js";
import type { Clock } from "../core/time.js";
import { documentClock, measureWallClockOffsetMs, rfc3339 } from "../core/time.js";
import { SUPPORTED_PROTOCOL_VERSION, SUPPORTED_SCHEMA_VERSION } from "../core/versions.js";
import type { DocumentAttestation } from "../activation/attestation.js";
import type { ActivationResult } from "../activation/gate.js";
import { activate, controlFetch } from "../activation/gate.js";
import type { SignedEnvelope } from "../activation/signed-envelope.js";
import type { ManifestIndex } from "../annotations/manifest.js";
import { buildEnvelope } from "../observation/envelope.js";
import type { Actor, BaselineReason, ObservationPayload, ObservationScope, RouteState } from "../observation/types.js";
import type { RedactionPipeline } from "../redaction/pipeline.js";
import type { TargetRegistry } from "../registry/targets.js";
import type { RouteTemplates } from "../routes/templates.js";
import type { TraceCoordinator } from "../trace/context.js";
import { ExportBuffer } from "../export/buffer.js";
import type { ExportRecord } from "../export/record.js";
import { AcknowledgementTracker, SequenceAllocator } from "../export/sequences.js";
import { IngestTransport } from "../export/transport.js";
import type { EmitOptions } from "./context.js";
import { NO_TARGET_OR_TRACE } from "./context.js";
import { ObservationCore } from "./core.js";
import type { ChildRecord, FrameAttachment } from "./frames.js";
import { FrameCoordinator } from "./frames.js";
import { runPropagationProbe } from "./probe.js";
import type { ReadinessState, RuntimeHealth } from "./readiness.js";
import { ReadinessController } from "./readiness.js";
import type { DedicatedWorkerLike, WorkerCapability } from "./workers.js";
import { WorkerCapabilityGrants } from "./workers.js";

/** Marks the document that already owns a runtime, so a second copy fails closed. */
const DOCUMENT_OWNER_MARKER = Symbol.for("utrace.runtime.owner");

interface OwnedDocument {
  [DOCUMENT_OWNER_MARKER]?: { readonly runtime_id: string; readonly protocol_version: string };
}

const BASELINE_ACCEPT_TIMEOUT_MS = 10_000;
const FLUSH_DEBOUNCE_MS = 25;
const GAP_BASELINE_INTERVAL_MS = 1000;

/** Bounded recovery window before the runtime reports ingestion unavailable. */
const RECOVERY_DEADLINE_MS = 30_000;
const RECOVERY_BACKOFF_MS: readonly number[] = [250, 500, 1000, 2000, 4000, 8000];

/** Failures a reconnect attempt may outlast within the recovery window. */
const RECOVERABLE_FAILURES: ReadonlySet<RuntimeFailureCode> = new Set<RuntimeFailureCode>([
  "ingestion_unavailable",
  "activation_endpoint_unavailable",
  "credential_rejected",
]);

/**
 * Claims a replacement activation must repeat, because this document's
 * observation core, redaction pipeline, and frame coordinator were built from
 * them.
 */
const PINNED_ATTESTATION_CLAIMS = [
  "client_id",
  "client_user_id",
  "customer_account_id",
  "session_id",
  "preview_environment_id",
  "preview_runtime_allocation_id",
  "preview_version_id",
  "origin",
  "protocol_version",
  "schema_version",
  "annotation_manifest_version",
  "annotation_manifest_digest",
  "redaction_version",
  "policy_version",
] as const satisfies readonly (keyof DocumentAttestation)[];

/** Whether a reconnect kept the current stream or had to start a new one. */
type StreamContinuity = "same_stream" | "new_stream";

export class SharedObservationRuntime {
  readonly view: BrowserView;
  readonly clock: Clock;
  readonly random: RandomSource;
  readonly #config: BrowserSdkConfig;
  readonly #readiness = new ReadinessController();
  readonly #runtimeId: string;
  #exportSequences = new SequenceAllocator();
  #acks = new AcknowledgementTracker();
  #browserStreamId: string;

  #activation: ActivationResult | null = null;
  #core: ObservationCore | null = null;
  #buffer: ExportBuffer | null = null;
  #transport: IngestTransport | null = null;
  #frameCoordinator: FrameCoordinator | null = null;
  #stopFrameCoordinator: (() => void) | null = null;
  #workerGrants: WorkerCapabilityGrants | null = null;
  #flushTimer: number | null = null;
  #pagehideBound: (() => void) | null = null;
  #lastGapBaselineAtMs = Number.NEGATIVE_INFINITY;
  #droppedRangeCount = 0;

  private constructor(view: BrowserView, config: BrowserSdkConfig) {
    this.view = view;
    this.clock = documentClock(view);
    this.random = webCryptoRandom(view.crypto);
    this.#config = config;
    this.#runtimeId = uuidv7(this.clock.wallNowMs(), this.random);
    this.#browserStreamId = uuidv7(this.clock.wallNowMs(), this.random);
  }

  /**
   * Claims this document. A second runtime in the same document, from any bundle
   * copy, is a `duplicate_runtime_or_hook` failure rather than a second path.
   */
  static create(view: BrowserView, config: BrowserSdkConfig): SharedObservationRuntime {
    const owned = view.document as unknown as OwnedDocument;
    const existing = owned[DOCUMENT_OWNER_MARKER];
    if (existing !== undefined) {
      throw new UTraceRuntimeError("duplicate_runtime_or_hook", "this document already hosts a uTrace runtime", {
        existing_runtime_id: existing.runtime_id,
        existing_protocol_version: existing.protocol_version,
      });
    }
    const runtime = new SharedObservationRuntime(view, config);
    Object.defineProperty(view.document, DOCUMENT_OWNER_MARKER, {
      value: { runtime_id: runtime.#runtimeId, protocol_version: config.protocolVersion },
      enumerable: false,
      writable: false,
      configurable: true,
    });
    return runtime;
  }

  get state(): ReadinessState {
    return this.#readiness.state;
  }

  get runtimeId(): string {
    return this.#runtimeId;
  }

  get browserStreamId(): string {
    return this.#browserStreamId;
  }

  get pageProof(): SignedEnvelope | null {
    return this.#activation?.pageProof ?? null;
  }

  get manifest(): ManifestIndex {
    return this.#requireCore().manifest;
  }

  get redaction(): RedactionPipeline {
    return this.#requireCore().redaction;
  }

  get registry(): TargetRegistry {
    return this.#requireCore().registry;
  }

  get routes(): RouteTemplates {
    return this.#requireCore().routes;
  }

  get trace(): TraceCoordinator {
    return this.#requireCore().trace;
  }

  get attestationDocumentAttachmentId(): string {
    return this.#require(this.#activation, "activation").attestation.document_attachment_id;
  }

  currentRoute = (): RouteState => this.#requireCore().currentRoute();

  actor = (): Actor => this.#requireCore().actor();

  setActor(actor: Actor): void {
    this.#requireCore().setActor(actor);
  }

  markSensitiveSubtree(element: Element): void {
    this.#requireCore().markSensitiveSubtree(element);
  }

  emit = (payload: ObservationPayload, options: EmitOptions): void => {
    this.#requireCore().emit(payload, options);
  };

  /** Runs the activation gate and brings the runtime to `ready`, or fails. */
  async start(): Promise<void> {
    this.#readiness.transition("activating");
    const activation = await this.#activateDocument();
    this.#adopt(activation);
    const core = this.#requireCore();
    core.installHooks();
    this.#readiness.setComponent("hooks_installed_once", core.hooksInstalledOnce);
    await this.#createTransport().open(activation.credential);
    this.#attachTopology();
    core.seedRegistry();
    await this.#bringUp("activation");
  }

  /**
   * Resume reconnects the way recovery does, so an unexpired credential reopens
   * the paused stream at its watermark and only a lapsed one starts a new
   * stream. Either way it emits a fresh baseline and proves readiness again.
   */
  async resume(): Promise<void> {
    if (this.#readiness.state !== "paused") return;
    this.#readiness.transition("activating");
    this.#markStaticComponents();
    this.#readiness.setComponent("hooks_installed_once", this.#requireCore().hooksInstalledOnce);
    this.#requireCore().setSuspended(false);
    await this.#reconnect();
    await this.#bringUp("resume");
  }

  /** The shared tail of activation and resume: baseline, probe, registry, ready. */
  async #bringUp(reason: Extract<BaselineReason, "activation" | "resume">): Promise<void> {
    const core = this.#requireCore();
    await this.#emitBaseline(reason);
    core.streamRegistryChanges();
    await this.#runProbe();
    // A registry is consistent when a fresh scan of the document finds nothing
    // it does not already hold. A document with no registrable element is
    // consistent, which is not the same as an unpopulated registry: the
    // application gate withholds the product subtree until the runtime is
    // ready, so at activation the document is legitimately bare and the
    // targets it will mount arrive later as streamed registry changes.
    const undiscovered = core.registry.discoverVisualTargets(this.view.document);
    this.#readiness.setComponent("registry_consistent", undiscovered === 0);
    this.#becomeReady();
    core.startPeriodicWork();
  }

  /**
   * Claims `ready`. A stream that dropped while readiness was being proven
   * reported its disconnect outside `ready`, so recovery starts here instead.
   */
  #becomeReady(): void {
    this.#readiness.transition("ready");
    if (!this.#require(this.#transport, "telemetry transport").connected) void this.#recover();
  }

  /**
   * Bounded recovery after the telemetry WebSocket drops. Only a `ready`
   * runtime recovers: during `activating` the bring-up in progress owns the
   * transport and fails explicitly when its acknowledgement never arrives, and
   * `paused`, `failed`, and `terminated` export nothing.
   */
  async #recover(): Promise<void> {
    if (this.#readiness.state !== "ready") return;
    this.#readiness.transition("recovering");
    this.#readiness.setComponent("ingestion_acknowledging", false);
    try {
      const continuity = await this.#reconnect();
      if (continuity === "new_stream") {
        await this.#bringUp("activation");
        return;
      }
      this.#readiness.setComponent("ingestion_acknowledging", true);
      this.#becomeReady();
      await this.#require(this.#transport, "telemetry transport").flush();
    } catch (cause) {
      // `state` is read through the public getter: the guard above narrowed
      // the private path, but `terminate()` may have run during recovery.
      if (this.state === "terminated") return;
      this.#fail(
        cause instanceof UTraceRuntimeError
          ? cause
          : new UTraceRuntimeError("ingestion_unavailable", "telemetry stream recovery failed", {
              cause: String(cause),
            }),
      );
    }
  }

  /**
   * Reconnects the export path within the bounded recovery window.
   *
   * While the telemetry credential is unexpired, every attempt reopens the
   * current stream with that credential: the same activation, document
   * attachment, `browser_stream_id`, and buffer resume from ingestion's last
   * contiguous acknowledgement, and no attestation runs. Only a credential that
   * has expired, or that ingestion rejects, sends the runtime through a fresh
   * attestation, and the new activation starts a new stream. Integrity and
   * other non-recoverable failures end recovery at once.
   */
  async #reconnect(): Promise<StreamContinuity> {
    const deadline = this.clock.wallNowMs() + RECOVERY_DEADLINE_MS;
    let continuity: StreamContinuity = "same_stream";
    let credentialRejected = false;
    for (let attempt = 1; ; attempt += 1) {
      if (this.#readiness.state === "terminated") {
        throw new ExportError("ingestion_unavailable", "the runtime terminated during recovery", { attempts: attempt });
      }
      try {
        const lapsed = this.#require(this.#activation, "activation").credential.expired(this.clock.wallNowMs());
        if (credentialRejected || lapsed) {
          await this.#beginNewStream();
          continuity = "new_stream";
          credentialRejected = false;
        }
        await this.#createTransport().open(this.#require(this.#activation, "activation").credential);
        return continuity;
      } catch (cause) {
        if (!(cause instanceof UTraceRuntimeError) || !RECOVERABLE_FAILURES.has(cause.code)) throw cause;
        if (cause.code === "credential_rejected") credentialRejected = true;
        const backoff = RECOVERY_BACKOFF_MS[Math.min(attempt - 1, RECOVERY_BACKOFF_MS.length - 1)] ?? 8000;
        if (this.clock.wallNowMs() + backoff >= deadline) {
          throw new ExportError("ingestion_unavailable", "bounded recovery expired without an accepted telemetry stream", {
            attempts: attempt,
            recovery_deadline_ms: RECOVERY_DEADLINE_MS,
            browser_stream_id: this.#browserStreamId,
            last_failure: cause.code,
          });
        }
        await new Promise<void>((resolve) => {
          this.view.setTimeout(resolve, backoff);
        });
      }
    }
  }

  /**
   * Starts a new stream under a fresh attestation, exactly as a new document
   * runtime would: a new `browser_stream_id`, export sequence from one, an
   * empty buffer, and a fresh baseline at bring-up.
   *
   * A fresh attestation is a new observation activation with a new document
   * attachment, and a new activation can never resume the old stream. The old
   * stream's unacknowledged and unsent records were captured under the old
   * activation, so they are discarded rather than exported under the new one.
   * The old stream records no terminal sequence: ingestion keeps its last
   * contiguous acknowledgement as the end of what it delivered, its missing
   * tail stays explicit, and the interval is not reconstructed.
   */
  async #beginNewStream(): Promise<void> {
    const activation = await this.#activateDocument();
    const current = this.#require(this.#activation, "activation").attestation;
    for (const claim of PINNED_ATTESTATION_CLAIMS) {
      if (activation.attestation[claim] !== current[claim]) {
        throw new ActivationError(
          "attestation_invalid_or_expired",
          "the replacement attestation does not pin what this document's runtime was built from",
          { claim },
        );
      }
    }
    this.#activation = activation;
    this.#browserStreamId = uuidv7(this.clock.wallNowMs(), this.random);
    this.#exportSequences = new SequenceAllocator();
    this.#acks = new AcknowledgementTracker();
    this.#buffer = new ExportBuffer(activation.credential.limits.max_observation_bytes);
    this.#requireCore().attachDocument(activation.attestation.document_attachment_id);
    this.#markStaticComponents();
    this.#readiness.setComponent("baseline_accepted", false);
    this.#readiness.setComponent("trace_probe_passed", false);
  }

  #activateDocument(): Promise<ActivationResult> {
    return activate({
      config: this.#config,
      documentOrigin: this.view.location.origin,
      fetchControl: controlFetch(this.view),
      subtle: this.view.crypto.subtle,
      random: this.random,
      nowMs: () => this.clock.wallNowMs(),
    });
  }

  #adopt(activation: ActivationResult): void {
    this.#activation = activation;
    this.#buffer = new ExportBuffer(activation.credential.limits.max_observation_bytes);
    this.#core = new ObservationCore(
      this.view,
      this.clock,
      this.random,
      this.#config,
      activation,
      {
        runtime_id: this.#runtimeId,
        document_attachment_id: activation.attestation.document_attachment_id,
        frame_id: "top",
      },
      {
        deliver: (record: ExportRecord): void => {
          this.#deliver(record);
        },
      },
      {
        onRouteCommitted: (): void => {
          void this.#emitBaseline("settled_route_transition");
        },
        onRecordDelivered: (): void => {
          this.#scheduleFlush();
        },
      },
    );
    this.#markStaticComponents();
  }

  #markStaticComponents(): void {
    const attestation = this.#require(this.#activation, "activation").attestation;
    this.#readiness.setComponent("attestation_current", true);
    this.#readiness.setComponent("credential_current", true);
    this.#readiness.setComponent("protocol_compatible", attestation.protocol_version === SUPPORTED_PROTOCOL_VERSION);
    this.#readiness.setComponent("schema_compatible", attestation.schema_version === SUPPORTED_SCHEMA_VERSION);
    this.#readiness.setComponent("single_top_coordinator", this.view.top === this.view);
    // The pilot declares no required child frame or worker adapter. Both stay
    // satisfied until a required attachment is declared and then lost.
    this.#readiness.setComponent("required_frames_connected", true);
    this.#readiness.setComponent("required_workers_connected", true);
  }

  #deliver(record: ExportRecord): void {
    const buffer = this.#buffer;
    if (buffer === null) return;
    try {
      buffer.enqueue(record);
    } catch (cause) {
      if (cause instanceof ExportError) {
        this.#fail(cause);
        return;
      }
      throw cause;
    }
  }

  /**
   * Builds an unopened transport for the current stream and binds the bounded
   * `pagehide` flush to it. A previous transport is abandoned first.
   */
  #createTransport(): IngestTransport {
    const activation = this.#require(this.#activation, "activation");
    const buffer = this.#require(this.#buffer, "export buffer");
    this.#transport?.abandon();
    const transport = new IngestTransport(
      this.view,
      this.#scope(),
      {
        browser_stream_id: this.#browserStreamId,
        runtime_id: this.#runtimeId,
        document_attachment_id: activation.attestation.document_attachment_id,
      },
      buffer,
      this.#exportSequences,
      this.#acks,
      {
        onAcknowledged: () => {
          this.#readiness.setComponent("ingestion_acknowledging", true);
        },
        onFlowControl: () => {
          // Flow control changes only the transport's state. Load shedding
          // happens on the next flush attempt, so sustained pressure sheds
          // droppable records and produces one explicit gap for the range.
          this.#scheduleFlush();
        },
        onFailure: (error) => {
          this.#fail(error);
        },
        onDisconnected: () => {
          void this.#recover();
        },
        batchId: () => uuidv7(this.clock.wallNowMs(), this.random),
      },
    );
    this.#transport = transport;
    if (this.#pagehideBound !== null) this.view.removeEventListener("pagehide", this.#pagehideBound);
    this.#pagehideBound = (): void => {
      transport.flushOnPagehide();
    };
    this.view.addEventListener("pagehide", this.#pagehideBound);
    return transport;
  }

  /**
   * The top-document runtime is the coordinator. It accepts normalized records
   * from instrumented child frames and re-envelopes them under their own
   * runtime, attachment, and frame identity.
   */
  #attachTopology(): void {
    const attestation = this.#require(this.#activation, "activation").attestation;
    this.#workerGrants = new WorkerCapabilityGrants(
      this.#runtimeId,
      attestation.document_attachment_id,
      this.#config.schemaVersion,
      this.random,
    );
    if (this.view.top !== this.view || this.#frameCoordinator !== null) return;
    const coordinator = new FrameCoordinator(
      this.view,
      this.view.crypto.subtle,
      this.#config.schemaVersion,
      {
        client_id: attestation.client_id,
        client_user_id: attestation.client_user_id,
        customer_account_id: attestation.customer_account_id,
        session_id: attestation.session_id,
        preview_environment_id: attestation.preview_environment_id,
        preview_runtime_allocation_id: attestation.preview_runtime_allocation_id,
      },
      {
        onChildObservation: (attachment, record) => {
          this.#forwardChildRecord(attachment, record);
        },
        onAttachmentLost: () => {
          this.#readiness.setComponent("required_frames_connected", false);
        },
        onRejected: () => {
          // A rejected child never joins the runtime. It reports its own
          // activation failure on its own control path.
        },
      },
    );
    this.#frameCoordinator = coordinator;
    this.#stopFrameCoordinator = coordinator.listen();
  }

  /** Re-envelopes and exports one record produced by an instrumented child frame. */
  #forwardChildRecord(attachment: FrameAttachment, record: ChildRecord): void {
    if (this.#readiness.state !== "ready" && this.#readiness.state !== "activating") return;
    this.#deliver({
      envelope: buildEnvelope({
        observation_id: record.observation_id,
        observation_type: record.observation_type,
        identity: {
          runtime_id: attachment.runtime_id,
          document_attachment_id: attachment.document_attachment_id,
          frame_id: attachment.frame_id,
        },
        source_sequence: record.source_sequence,
        occurred_at: record.occurred_at,
        monotonic_offset: record.monotonic_offset,
        route_revision: record.route_revision,
        visibility_state: record.visibility_state,
        target_id: null,
        target_revision: null,
        trace_id: null,
        span_id: null,
      }),
      body: record.body,
    });
    this.#scheduleFlush();
  }

  /**
   * Grants one dedicated worker its one-time child capability. The worker may
   * report only work this document causally assigned to it.
   */
  grantWorkerCapability(worker: DedicatedWorkerLike, expiresAt: string): WorkerCapability {
    return this.#require(this.#workerGrants, "worker capability grants").grant(worker, expiresAt);
  }

  #scope(): ObservationScope {
    const attestation = this.#require(this.#activation, "activation").attestation;
    return {
      client_id: attestation.client_id,
      client_user_id: attestation.client_user_id,
      customer_account_id: attestation.customer_account_id,
      session_id: attestation.session_id,
      preview_environment_id: attestation.preview_environment_id,
      preview_runtime_allocation_id: attestation.preview_runtime_allocation_id,
      ...(attestation.preview_version_id === null ? {} : { preview_version_id: attestation.preview_version_id }),
      browser_stream_id: this.#browserStreamId,
      schema_version: attestation.schema_version,
      policy_version: attestation.policy_version,
      redaction_version: attestation.redaction_version,
    };
  }

  async #emitBaseline(reason: BaselineReason): Promise<void> {
    const activation = this.#require(this.#activation, "activation");
    await this.#requireCore().emitBaseline(reason, activation.credential.limits.max_observation_bytes);
    // Activation and resume both require an accepted baseline before the
    // runtime may claim readiness; a later checkpoint does not block.
    if (reason !== "activation" && reason !== "resume") return;
    await this.#awaitAcknowledgement();
    this.#readiness.setComponent("baseline_accepted", true);
  }

  async #awaitAcknowledgement(): Promise<void> {
    const transport = this.#require(this.#transport, "telemetry transport");
    await transport.flush();
    const target = this.#exportSequences.produced;
    const deadline = this.clock.wallNowMs() + BASELINE_ACCEPT_TIMEOUT_MS;
    while (this.#acks.acknowledged < target) {
      if (this.clock.wallNowMs() > deadline) {
        throw new UTraceRuntimeError("baseline_rejected", "ingestion did not acknowledge the baseline", {
          target_export_sequence: target,
          acknowledged_export_sequence: this.#acks.acknowledged,
        });
      }
      await new Promise<void>((resolve) => {
        this.view.setTimeout(resolve, 25);
      });
      await transport.flush();
    }
  }

  async #runProbe(): Promise<void> {
    await runPropagationProbe(this.view, this.#requireCore().trace, this.view.fetch.bind(this.view));
    this.#readiness.setComponent("trace_probe_passed", true);
  }

  #scheduleFlush(): void {
    if (this.#flushTimer !== null) return;
    this.#flushTimer = this.view.setTimeout(() => {
      this.#flushTimer = null;
      if (this.#transport?.flowControlPaused === true) this.#buffer?.dropDroppable("flow_control");
      this.#reportGaps();
      void this.#transport?.flush();
    }, FLUSH_DEBOUNCE_MS);
  }

  /** Turns dropped ranges into explicit gap records followed by a fresh baseline. */
  #reportGaps(): void {
    const buffer = this.#buffer;
    if (buffer === null) return;
    const ranges = buffer.takeDroppedRanges();
    if (ranges.length === 0) return;
    // One fresh baseline per pressure episode: sustained pressure already sheds
    // load, and a baseline per flush cycle would add to it.
    const nowMs = this.clock.monotonicNowMs();
    const baselineDue = nowMs - this.#lastGapBaselineAtMs > GAP_BASELINE_INTERVAL_MS;
    if (baselineDue) this.#lastGapBaselineAtMs = nowMs;
    this.#droppedRangeCount += ranges.length;
    for (const range of ranges) {
      this.emit(
        {
          type: "observation.gap",
          body: {
            gap_runtime_id: range.runtime_id,
            gap_first_source_sequence: range.first_source_sequence,
            gap_last_source_sequence: range.last_source_sequence,
            count: range.count,
            observation_class: range.observation_class,
            reason: range.reason,
            baseline_follows: baselineDue,
          },
        },
        NO_TARGET_OR_TRACE,
      );
    }
    if (baselineDue) void this.#emitBaseline("delivery_gap");
  }

  #fail(error: UTraceRuntimeError): void {
    this.#core?.setSuspended(true);
    this.#readiness.fail(error.code, error.message);
  }

  /**
   * Stops session-correlated observation and export after acknowledged
   * transition cleanup. Activity during the pause is not reconstructed.
   */
  async pause(): Promise<void> {
    if (this.#readiness.state !== "ready") return;
    try {
      await this.#transport?.flush();
      await this.#awaitAcknowledgement();
    } catch (cause) {
      // Cleanup must be acknowledged before the runtime may claim it paused
      // cleanly. An unacknowledged tail is a typed failure, not a quiet pause.
      this.#stopForPause();
      this.#fail(
        cause instanceof UTraceRuntimeError
          ? cause
          : new UTraceRuntimeError("ingestion_unavailable", "pause cleanup was not acknowledged", {
              cause: String(cause),
            }),
      );
      return;
    }
    this.#stopForPause();
    this.#readiness.resetComponents();
    this.#readiness.transition("paused");
  }

  #stopForPause(): void {
    this.#core?.stopPeriodicWork();
    this.#core?.setSuspended(true);
    this.#transport?.close("paused");
  }

  /** Terminal cleanup. The SDK returns to an inert unbound state. */
  terminate(): void {
    this.#core?.setSuspended(true);
    this.#core?.teardown();
    this.#transport?.close("terminated");
    this.#stopFrameCoordinator?.();
    this.#stopFrameCoordinator = null;
    this.#frameCoordinator = null;
    this.#workerGrants?.revokeAll();
    if (this.#flushTimer !== null) this.view.clearTimeout(this.#flushTimer);
    if (this.#pagehideBound !== null) this.view.removeEventListener("pagehide", this.#pagehideBound);
    this.#readiness.transition("terminated");
  }

  /** Operational metadata only. Never product content. */
  health(): RuntimeHealth {
    const activation = this.#activation;
    const core = this.#core;
    return {
      state: this.#readiness.state,
      browser_stream_id: this.#browserStreamId,
      runtime_id: this.#runtimeId,
      document_attachment_id: activation?.attestation.document_attachment_id ?? "",
      latest_source_sequence: core?.sourceSequenceProduced ?? 0,
      latest_export_sequence: this.#exportSequences.produced,
      acknowledged_export_sequence: this.#acks.acknowledged,
      buffer_utilization: this.#buffer?.utilization ?? 0,
      dropped_range_count: this.#droppedRangeCount,
      components: this.#readiness.components,
      hooks: core?.hookStatus ?? {
        fetch: false,
        xhr: false,
        history: false,
        errors: false,
        interactions: false,
        mutations: false,
      },
      annotation_manifest_version: activation?.manifest.manifest_version ?? "",
      policy_version: activation?.attestation.policy_version ?? "",
      redaction_version: activation?.attestation.redaction_version ?? "",
      protocol_version: this.#config.protocolVersion,
      schema_version: this.#config.schemaVersion,
      wall_clock_offset_ms: Math.round(measureWallClockOffsetMs(this.clock)),
      failure_code: this.#readiness.failureCode,
      failure_detail: this.#readiness.failureDetail,
      checked_at: rfc3339(this.clock.wallNowMs()),
    };
  }

  #requireCore(): ObservationCore {
    return this.#require(this.#core, "observation core");
  }

  #require<T>(value: T | null, what: string): T {
    if (value === null) {
      throw new UTraceRuntimeError("attestation_invalid_or_expired", `${what} is unavailable before activation`, {
        state: this.#readiness.state,
      });
    }
    return value;
  }
}
