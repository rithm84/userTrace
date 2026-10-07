/**
 * The observation engine one document runs.
 *
 * The core owns everything that is identical in a top-level document and in an
 * instrumented child frame: the signed manifest, the redaction pipeline, the
 * target registry, route normalization, trace creation, the single-owner
 * capture hooks, `source_sequence`, and baseline projection.
 *
 * It does not decide where records go. A top-document runtime hands it an
 * export buffer; a child frame runtime hands it the authenticated
 * `MessageChannel` to the coordinator. That keeps the shared runtime the sole
 * producer of automatic browser observations with exactly one export path per
 * page.
 */

import type { BrowserSdkConfig } from "../core/config.js";
import type { BrowserView } from "../core/dom.js";
import type { RandomSource } from "../core/ids.js";
import { uuidv7 } from "../core/ids.js";
import type { Clock } from "../core/time.js";
import { rfc3339 } from "../core/time.js";
import { GEOMETRY_PUBLISH_WINDOW_MS } from "../core/limits.js";
import type { AttestationResult } from "../activation/gate.js";
import { ManifestIndex } from "../annotations/manifest.js";
import { readElementAnnotations } from "../annotations/attributes.js";
import { buildEnvelope } from "../observation/envelope.js";
import type {
  Actor,
  BaselineReason,
  ObservationPayload,
  RouteState,
  VisibilityState,
} from "../observation/types.js";
import { RedactionPipeline } from "../redaction/pipeline.js";
import { TaintRegistry } from "../redaction/taint.js";
import { TargetRegistry } from "../registry/targets.js";
import { RouteTemplates } from "../routes/templates.js";
import { TraceCoordinator } from "../trace/context.js";
import { chunkBaseline } from "../export/chunking.js";
import type { ExportRecord } from "../export/record.js";
import { SequenceAllocator } from "../export/sequences.js";
import { installErrorHook } from "../hooks/errors.js";
import type { HookStatus } from "../hooks/install.js";
import { HookInstaller } from "../hooks/install.js";
import { installInteractionHook } from "../hooks/interactions.js";
import { installMutationHook } from "../hooks/mutations.js";
import { installHistoryHook } from "../hooks/navigation.js";
import { installFetchHook, installXhrHook } from "../hooks/network.js";
import { buildBaseline } from "./baseline.js";
import type { EmitOptions, RuntimeContext } from "./context.js";
import { NO_TARGET_OR_TRACE } from "./context.js";
import { PerformanceCollector } from "./performance.js";

/** Where the core delivers a finished record. */
export interface RecordSink {
  readonly deliver: (record: ExportRecord) => void;
}

/** Identity of this document's runtime inside the browser stream. */
export interface CoreIdentity {
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly frame_id: string;
}

export interface CoreCallbacks {
  /** Called after a route transition commits, so the owner can checkpoint. */
  readonly onRouteCommitted: () => void;
  /** Called when a record is delivered, so the owner can schedule a flush. */
  readonly onRecordDelivered: () => void;
}

export class ObservationCore implements RuntimeContext {
  readonly view: BrowserView;
  readonly clock: Clock;
  readonly random: RandomSource;
  readonly manifest: ManifestIndex;
  readonly redaction: RedactionPipeline;
  readonly registry: TargetRegistry;
  readonly routes: RouteTemplates;
  readonly trace: TraceCoordinator;

  readonly #config: BrowserSdkConfig;
  readonly #sink: RecordSink;
  readonly #callbacks: CoreCallbacks;
  readonly #taint = new TaintRegistry();
  readonly #sourceSequences = new SequenceAllocator();
  readonly #hooks: HookInstaller;
  #identity: CoreIdentity;
  #route: RouteState;
  #routeRevision = 1;
  #actor: Actor = "user";
  #suspended = false;
  #registryStreaming = false;
  #stopPeriodic: (() => void) | null = null;

  constructor(
    view: BrowserView,
    clock: Clock,
    random: RandomSource,
    config: BrowserSdkConfig,
    attestation: AttestationResult,
    identity: CoreIdentity,
    sink: RecordSink,
    callbacks: CoreCallbacks,
  ) {
    this.view = view;
    this.clock = clock;
    this.random = random;
    this.#config = config;
    this.#sink = sink;
    this.#callbacks = callbacks;
    this.#identity = identity;
    this.manifest = new ManifestIndex(attestation.manifest);
    this.redaction = new RedactionPipeline(attestation.manifest.redaction, this.#taint);
    this.registry = new TargetRegistry(view, this.manifest, this.redaction, random);
    this.routes = new RouteTemplates(attestation.manifest.route_templates);
    this.trace = new TraceCoordinator(random, () => clock.monotonicNowMs());
    this.#hooks = new HookInstaller(identity.runtime_id);
    this.#route = this.routes.resolve(new URL(view.location.href), this.redaction);
  }

  /**
   * Moves the core onto the document attachment of a replacement activation.
   * Records emitted from here carry it; the document, runtime, hooks, target
   * registry, and `source_sequence` continue unchanged.
   */
  attachDocument(documentAttachmentId: string): void {
    this.#identity = { ...this.#identity, document_attachment_id: documentAttachmentId };
  }

  get sourceSequenceProduced(): number {
    return this.#sourceSequences.produced;
  }

  get hookStatus(): HookStatus {
    return this.#hooks.status();
  }

  get hooksInstalledOnce(): boolean {
    return this.#hooks.allInstalled();
  }

  currentRoute = (): RouteState => this.#route;

  routeRevision = (): number => this.#routeRevision;

  actor = (): Actor => this.#actor;

  setActor(actor: Actor): void {
    this.#actor = actor;
  }

  /** While suspended the core produces no session-correlated observation. */
  setSuspended(suspended: boolean): void {
    this.#suspended = suspended;
  }

  markSensitiveSubtree(element: Element): void {
    this.#taint.taintSubtree(element);
  }

  /** Installs each capture hook exactly once. A duplicate throws. */
  installHooks(): void {
    this.#hooks.install("fetch", (runtimeId) => installFetchHook(this, runtimeId));
    this.#hooks.install("xhr", (runtimeId) => installXhrHook(this, runtimeId));
    this.#hooks.install("history", (runtimeId) => installHistoryHook(this, runtimeId));
    this.#hooks.install("errors", () => installErrorHook(this));
    this.#hooks.install("interactions", () => installInteractionHook(this));
    this.#hooks.install("mutations", () => installMutationHook(this));
  }

  /** Taints sensitive surfaces, mounts annotated targets, discovers visual ones. */
  seedRegistry(): void {
    for (const element of this.view.document.querySelectorAll("[data-utrace-sensitive]")) {
      this.#taint.taintSubtree(element);
    }
    for (const surface of this.manifest.manifest.surfaces) {
      if (!surface.sensitive) continue;
      for (const element of this.view.document.querySelectorAll(`[data-utrace-surface="${surface.surface_id}"]`)) {
        this.#taint.taintSubtree(element);
      }
    }
    this.#registerAnnotatedTargets(this.view.document);
    this.registry.discoverVisualTargets(this.view.document);
  }

  /**
   * Registers every declared target in `root`, including `root` itself:
   * product targets (`data-utrace-target`) and visual-only ones
   * (`data-utrace-visual-target`), each under its declared definition.
   *
   * Declared registration runs before automatic discovery, because an
   * annotated element must carry its own definition: a visual-only target
   * claimed by discovery has no definition id, so the model could never
   * resolve it by the name the client declared.
   */
  #registerAnnotatedTargets(root: ParentNode): void {
    const selector = "[data-utrace-target],[data-utrace-visual-target]";
    const candidates =
      root instanceof Element && root.matches(selector)
        ? [root, ...root.querySelectorAll(selector)]
        : [...root.querySelectorAll(selector)];
    for (const element of candidates) {
      const annotations = readElementAnnotations(element, this.manifest);
      const definitionId = annotations.targetDefinitionId ?? annotations.visualTargetDefinitionId;
      if (definitionId === null) continue;
      this.registry.register(element, definitionId, annotations);
    }
  }

  /**
   * Streams registry changes. It is attached only after the initial baseline,
   * which already carries the mounted set, so seeding is not replayed.
   */
  streamRegistryChanges(): void {
    if (this.#registryStreaming) return;
    this.#registryStreaming = true;
    this.registry.onChange((change) => {
      this.emit(
        {
          type: "target.registry_change",
          body: {
            change: change.change,
            registry_revision: change.registry_revision,
            target: change.target ?? undefined,
            rejected_annotation: change.rejected_annotation ?? undefined,
          },
        },
        change.target === null
          ? NO_TARGET_OR_TRACE
          : {
              targetId: change.target.target_id,
              targetRevision: change.target.target_revision,
              traceId: null,
              spanId: null,
            },
      );
    });
  }

  emit = (payload: ObservationPayload, options: EmitOptions): void => {
    if (this.#suspended) return;
    const envelope = buildEnvelope({
      observation_id: uuidv7(this.clock.wallNowMs(), this.random),
      observation_type: payload.type,
      identity: this.#identity,
      source_sequence: this.#sourceSequences.allocate(),
      occurred_at: rfc3339(this.clock.wallNowMs()),
      monotonic_offset: Math.round(this.clock.monotonicNowMs() * 1000) / 1000,
      route_revision: this.#routeRevision,
      visibility_state: this.#visibility(),
      target_id: options.targetId,
      target_revision: options.targetRevision,
      trace_id: options.traceId,
      span_id: options.spanId,
    });
    this.#sink.deliver({ envelope, body: payload.body });
    this.#callbacks.onRecordDelivered();
  };

  commitRouteChange = (transition: "push" | "replace" | "pop" | "load", actor: Actor): void => {
    const previous = this.#route.route_template;
    this.#route = this.routes.resolve(new URL(this.view.location.href), this.redaction);
    this.#routeRevision += 1;
    this.emit(
      {
        type: "route.change",
        body: { actor, transition, route: this.#route, previous_route_template: previous, committed: true },
      },
      NO_TARGET_OR_TRACE,
    );
    this.#callbacks.onRouteCommitted();
  };

  onDomMutation = (records: readonly MutationRecord[]): void => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        this.#taint.onNodeAdded(node);
        if (!(node instanceof Element)) continue;
        if (node.hasAttribute("data-utrace-sensitive")) this.#taint.taintSubtree(node);
        // A framework mounts a subtree, not one element at a time, so the
        // annotated targets are usually descendants of the added node.
        this.#registerAnnotatedTargets(node);
        this.registry.discoverVisualTargets(node);
      }
    }
    this.registry.refreshAll();
  };

  /** Emits the baseline, splitting it into hashed chunks when it is oversized. */
  async emitBaseline(reason: BaselineReason, maxObservationBytes: number): Promise<void> {
    const baselineId = uuidv7(this.clock.wallNowMs(), this.random);
    const snapshot = buildBaseline({
      baselineId,
      reason,
      view: this.view,
      manifest: this.manifest,
      redaction: this.redaction,
      registry: this.registry,
      route: this.#route,
    });
    const chunks = await chunkBaseline(baselineId, snapshot, maxObservationBytes, this.view.crypto.subtle);
    if (chunks.length === 0) {
      this.emit({ type: "baseline.snapshot", body: snapshot }, NO_TARGET_OR_TRACE);
      return;
    }
    for (const chunk of chunks) this.emit({ type: "baseline.chunk", body: chunk }, NO_TARGET_OR_TRACE);
  }

  /** Bounded performance batching plus geometry refresh on scroll, resize, and a timer. */
  startPeriodicWork(): void {
    this.stopPeriodicWork();
    const performance = new PerformanceCollector(this.view, () => this.clock.monotonicNowMs(), (payload) => {
      this.emit({ type: "performance.measurement", body: payload }, NO_TARGET_OR_TRACE);
    });
    performance.start();
    const onGeometry = (): void => {
      this.registry.refreshAll();
    };
    const timer = this.view.setInterval(onGeometry, GEOMETRY_PUBLISH_WINDOW_MS * 10);
    this.view.addEventListener("scroll", onGeometry, { passive: true });
    this.view.addEventListener("resize", onGeometry, { passive: true });
    this.#stopPeriodic = (): void => {
      performance.stop();
      this.view.clearInterval(timer);
      this.view.removeEventListener("scroll", onGeometry);
      this.view.removeEventListener("resize", onGeometry);
    };
  }

  stopPeriodicWork(): void {
    this.#stopPeriodic?.();
    this.#stopPeriodic = null;
  }

  teardown(): void {
    this.stopPeriodicWork();
    this.#hooks.uninstallAll();
  }

  get schemaVersion(): string {
    return this.#config.schemaVersion;
  }

  #visibility(): VisibilityState {
    return this.view.document.visibilityState === "visible" ? "visible" : "hidden";
  }
}
