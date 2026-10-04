/**
 * Runtime readiness and health.
 *
 * `ready` requires a current document attestation and telemetry credential,
 * compatible protocol and schemas, exactly one top coordinator, every required
 * frame and worker connected, each capture hook installed once, an accepted
 * initial baseline, an acknowledging ingestion WebSocket, a successful trace
 * propagation probe without duplicate injectors, and a consistent target
 * registry.
 *
 * Health reports operational metadata only. It never carries product content as
 * a diagnostic.
 */

import type { RuntimeFailureCode } from "../core/failure.js";
import { UTraceRuntimeError } from "../core/failure.js";
import type { HookStatus } from "../hooks/install.js";

export type ReadinessState = "dormant" | "activating" | "ready" | "paused" | "recovering" | "failed" | "terminated";

export interface ReadinessComponents {
  readonly attestation_current: boolean;
  readonly credential_current: boolean;
  readonly protocol_compatible: boolean;
  readonly schema_compatible: boolean;
  readonly single_top_coordinator: boolean;
  readonly required_frames_connected: boolean;
  readonly required_workers_connected: boolean;
  readonly hooks_installed_once: boolean;
  readonly baseline_accepted: boolean;
  readonly ingestion_acknowledging: boolean;
  readonly trace_probe_passed: boolean;
  readonly registry_consistent: boolean;
}

export interface RuntimeHealth {
  readonly state: ReadinessState;
  readonly browser_stream_id: string;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly latest_source_sequence: number;
  readonly latest_export_sequence: number;
  readonly acknowledged_export_sequence: number;
  readonly buffer_utilization: number;
  readonly dropped_range_count: number;
  readonly components: ReadinessComponents;
  readonly hooks: HookStatus;
  readonly annotation_manifest_version: string;
  readonly policy_version: string;
  readonly redaction_version: string;
  readonly protocol_version: string;
  readonly schema_version: string;
  readonly wall_clock_offset_ms: number;
  readonly failure_code: RuntimeFailureCode | null;
  readonly failure_detail: string | null;
  readonly checked_at: string;
}

const ALLOWED: Readonly<Record<ReadinessState, readonly ReadinessState[]>> = {
  dormant: ["activating", "terminated"],
  activating: ["ready", "failed", "terminated"],
  ready: ["paused", "recovering", "failed", "terminated"],
  paused: ["activating", "failed", "terminated"],
  recovering: ["ready", "failed", "terminated"],
  failed: ["terminated"],
  terminated: [],
};

const ALL_FALSE: ReadinessComponents = {
  attestation_current: false,
  credential_current: false,
  protocol_compatible: false,
  schema_compatible: false,
  single_top_coordinator: false,
  required_frames_connected: false,
  required_workers_connected: false,
  hooks_installed_once: false,
  baseline_accepted: false,
  ingestion_acknowledging: false,
  trace_probe_passed: false,
  registry_consistent: false,
};

export type ReadinessListener = (state: ReadinessState, failure: RuntimeFailureCode | null) => void;

export class ReadinessController {
  #state: ReadinessState = "dormant";
  #components: ReadinessComponents = ALL_FALSE;
  #failureCode: RuntimeFailureCode | null = null;
  #failureDetail: string | null = null;
  readonly #listeners: ReadinessListener[] = [];

  get state(): ReadinessState {
    return this.#state;
  }

  get components(): ReadinessComponents {
    return this.#components;
  }

  get failureCode(): RuntimeFailureCode | null {
    return this.#failureCode;
  }

  get failureDetail(): string | null {
    return this.#failureDetail;
  }

  onChange(listener: ReadinessListener): void {
    this.#listeners.push(listener);
  }

  /** True only when every listed component currently holds. */
  get combinedReady(): boolean {
    return Object.values(this.#components).every((value) => value);
  }

  setComponent(name: keyof ReadinessComponents, value: boolean): void {
    this.#components = { ...this.#components, [name]: value };
  }

  resetComponents(): void {
    this.#components = ALL_FALSE;
  }

  transition(next: ReadinessState): void {
    if (next === this.#state) return;
    if (!(ALLOWED[this.#state] as readonly string[]).includes(next)) {
      throw new UTraceRuntimeError("duplicate_runtime_or_hook", "illegal runtime readiness transition", {
        from: this.#state,
        to: next,
      });
    }
    if (next === "ready" && !this.combinedReady) {
      const missing = Object.entries(this.#components)
        .filter(([, value]) => !value)
        .map(([name]) => name);
      // Naming the components keeps the failure actionable: "ready requires
      // every readiness component" alone tells a client integrating the SDK
      // nothing about which one its preview is missing.
      throw new UTraceRuntimeError(
        "baseline_rejected",
        `ready requires every readiness component; missing ${missing.join(", ")}`,
        { missing: missing.join(",") },
      );
    }
    this.#state = next;
    if (next !== "failed") {
      this.#failureCode = null;
      this.#failureDetail = null;
    }
    for (const listener of this.#listeners) listener(next, this.#failureCode);
  }

  /** Records a typed failure and withdraws readiness. */
  fail(code: RuntimeFailureCode, detail: string): void {
    this.#failureCode = code;
    this.#failureDetail = detail;
    this.resetComponents();
    if (this.#state === "terminated" || this.#state === "failed") return;
    this.#state = "failed";
    for (const listener of this.#listeners) listener("failed", code);
  }
}
