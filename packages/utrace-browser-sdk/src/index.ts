/**
 * `@utrace/browser-sdk` — the embedded browser SDK.
 *
 * Before activation this module is an inert bootstrap: importing it installs no
 * observer or network hook, registers no target, buffers no product data,
 * exports no telemetry, and exposes no active extension bridge. Everything
 * starts from `activate()`, which runs the bounded pre-application gate.
 */

import type { BrowserSdkConfig } from "./core/config.js";
import { validateConfig } from "./core/config.js";
import type { BrowserView } from "./core/dom.js";
import { UnsupportedHostError } from "./core/failure.js";
import { AnnotationApi } from "./annotations/api.js";
import type { AnnotationSurface } from "./annotations/api.js";
import { ExtensionBridge } from "./bridge/extension.js";
import { FrameObservationRuntime } from "./runtime/frame-runtime.js";
import { SharedObservationRuntime } from "./runtime/runtime.js";
import type { ReadinessState, RuntimeHealth } from "./runtime/readiness.js";
import type { DedicatedWorkerLike, WorkerCapability } from "./runtime/workers.js";

export type { BrowserSdkConfig } from "./core/config.js";
export type { BrowserView } from "./core/dom.js";
export type { FailureDetail, RuntimeFailureCode } from "./core/failure.js";
export { ActivationError, ConfigurationError, ExportError, UnsupportedHostError, UTraceRuntimeError } from "./core/failure.js";
export type { ReadinessComponents, ReadinessState, RuntimeHealth } from "./runtime/readiness.js";
export type { DedicatedWorkerLike, WorkerCapability } from "./runtime/workers.js";
export type { ChildRecord, FrameAttachment } from "./runtime/frames.js";
export type {
  Actor,
  Observation,
  ObservationEnvelope,
  ObservationPayload,
  ObservationScope,
  ObservationType,
  SafeEntityRef,
  SafeValue,
  TargetGeometry,
  TargetSemanticState,
  TargetSummary,
} from "./observation/types.js";
export type { AnnotationResult, AnnotationSurface, MilestoneRequest, TargetRegistrationRequest } from "./annotations/api.js";
export { AnnotationApi, inertAnnotationSurface } from "./annotations/api.js";
export type { TargetQuery, TargetQueryResult, TargetRegistration } from "./registry/targets.js";
export type { LengthBucket, OutputClass, RedactionStep, ValueKind } from "./redaction/classes.js";
export { BRIDGE_FRAME_KINDS } from "./bridge/messages.js";
export type { BridgeFrameKind, BridgeRequest, BridgeTargetQuery } from "./bridge/messages.js";
export type { DetailQuery, DetailQueryFailure, DetailQueryResult } from "./bridge/detail-query.js";
export { SUPPORTED_PROTOCOL_VERSION, SUPPORTED_SCHEMA_VERSION } from "./core/versions.js";

/** An activated observation session for one document. */
export interface UTraceSession {
  readonly annotations: AnnotationSurface;
  readonly health: () => RuntimeHealth;
  /**
   * Grants one dedicated worker its one-time child capability. Shared Workers
   * and Service Workers are refused; per-operation context must travel with
   * each request or message instead.
   */
  readonly grantWorkerCapability: (worker: DedicatedWorkerLike, expiresAt: string) => WorkerCapability;
  /** Stops session-correlated observation and export after acknowledged cleanup. */
  readonly pause: () => Promise<void>;
  /**
   * Reopens the paused stream with the unexpired credential, or starts a new
   * stream under a fresh attestation when the credential lapsed, then emits a
   * fresh baseline and proves readiness again.
   */
  readonly resume: () => Promise<void>;
  readonly terminate: () => void;
}

/** The inert bootstrap the client application installs. */
export interface UTraceBootstrap {
  readonly state: () => ReadinessState;
  readonly activate: () => Promise<UTraceSession>;
}

/** An instrumented child frame's attachment to the top-document coordinator. */
export interface UTraceFrameAttachment {
  readonly runtimeId: string;
  readonly frameId: string;
  readonly attached: () => boolean;
  readonly terminate: () => void;
}

/**
 * Activates an instrumented child frame.
 *
 * The frame authenticates its own document and joins the top-document
 * coordinator; it obtains no telemetry credential and never exports. Call it
 * from inside the iframe, not from the top document.
 */
export const activateUTraceFrame = async (
  view: BrowserView,
  config: BrowserSdkConfig,
): Promise<UTraceFrameAttachment> => {
  const validated = validateConfig(config);
  const runtime = new FrameObservationRuntime(view, validated);
  await runtime.start();
  return {
    runtimeId: runtime.runtimeId,
    frameId: runtime.frameId,
    attached: (): boolean => runtime.attached,
    terminate: (): void => {
      runtime.terminate();
    },
  };
};

const requireBrowser = (view: BrowserView): void => {
  if (typeof view.crypto.subtle.importKey !== "function") {
    throw new UnsupportedHostError("WebCrypto SubtleCrypto", "this document has no SubtleCrypto implementation");
  }
  if (typeof view.WebSocket !== "function") {
    throw new UnsupportedHostError("WebSocket", "this document has no WebSocket implementation");
  }
};

/**
 * Creates the inert bootstrap.
 *
 * `activate()` completes the gate, installs the single-owner capture hooks,
 * emits the initial baseline, proves trace propagation, and opens the extension
 * bridge. If activation or readiness fails it rejects, and the prepared preview
 * must report the failure rather than booting as an unobserved substitute.
 */
export const createUTraceBootstrap = (view: BrowserView, config: BrowserSdkConfig): UTraceBootstrap => {
  const validated = validateConfig(config);
  let runtime: SharedObservationRuntime | null = null;
  let bridge: ExtensionBridge | null = null;

  return {
    state: (): ReadinessState => runtime?.state ?? "dormant",
    activate: async (): Promise<UTraceSession> => {
      requireBrowser(view);
      if (runtime !== null) {
        throw new UnsupportedHostError("one activation per bootstrap", "this bootstrap already activated a runtime");
      }
      const created = SharedObservationRuntime.create(view, validated);
      runtime = created;
      await created.start();
      const extensionBridge = new ExtensionBridge(created, {
        onPauseNotice: async (state: "paused" | "running" | "ended"): Promise<void> => {
          // The control plane owns the transition; the runtime follows it. An
          // ended session terminates the runtime rather than pausing it,
          // because a terminal state never resumes.
          if (state === "paused") await created.pause();
          else if (state === "running") await created.resume();
          else created.terminate();
        },
        onRejected: (): void => {
          // A rejected bridge message never changes observation state. The
          // extension surfaces its own binding failure on the control path.
        },
      });
      extensionBridge.listen();
      bridge = extensionBridge;
      return {
        annotations: new AnnotationApi(created),
        health: (): RuntimeHealth => created.health(),
        grantWorkerCapability: (worker: DedicatedWorkerLike, expiresAt: string): WorkerCapability =>
          created.grantWorkerCapability(worker, expiresAt),
        pause: (): Promise<void> => created.pause(),
        resume: (): Promise<void> => created.resume(),
        terminate: (): void => {
          bridge?.close();
          created.terminate();
        },
      };
    },
  };
};
