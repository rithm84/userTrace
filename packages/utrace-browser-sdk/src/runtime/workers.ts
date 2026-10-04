/**
 * Dedicated worker capability.
 *
 * A dedicated worker receives a one-time child capability from the document
 * that created it and may report only work causally assigned by that document.
 * Shared Workers and Service Workers never receive a global Interview Session
 * identity; relevant per-operation trace and session context must travel with
 * each request or message instead.
 */

import { UTraceRuntimeError } from "../core/failure.js";
import type { RandomSource } from "../core/ids.js";
import { opaqueId } from "../core/ids.js";

export const WORKER_CAPABILITY_KIND = "utrace.worker.capability";

/**
 * The only capability the runtime needs from a dedicated worker. Accepting the
 * narrow shape keeps the grant testable and makes it obvious that the runtime
 * neither reads from nor controls the worker.
 */
export type DedicatedWorkerLike = Pick<Worker, "postMessage">;

export interface WorkerCapability {
  readonly kind: typeof WORKER_CAPABILITY_KIND;
  readonly schema_version: string;
  readonly capability_id: string;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly expires_at: string;
}

/**
 * Grants one dedicated worker its single capability and a private port. A second
 * grant to the same worker is a duplicate-capability failure, not a refresh.
 */
export class WorkerCapabilityGrants {
  readonly #runtimeId: string;
  readonly #documentAttachmentId: string;
  readonly #schemaVersion: string;
  readonly #random: RandomSource;
  readonly #granted = new WeakSet<DedicatedWorkerLike>();
  readonly #ports = new Map<string, MessagePort>();
  #ordinal = 0;

  constructor(runtimeId: string, documentAttachmentId: string, schemaVersion: string, random: RandomSource) {
    this.#runtimeId = runtimeId;
    this.#documentAttachmentId = documentAttachmentId;
    this.#schemaVersion = schemaVersion;
    this.#random = random;
  }

  get grantedCount(): number {
    return this.#ports.size;
  }

  grant(worker: DedicatedWorkerLike, expiresAt: string): WorkerCapability {
    const kind = worker.constructor.name;
    if (kind === "SharedWorker" || kind === "ServiceWorker") {
      throw new UTraceRuntimeError(
        "required_frame_or_adapter_missing",
        "only a dedicated worker may hold a uTrace capability",
        { worker_kind: kind },
      );
    }
    if (this.#granted.has(worker)) {
      throw new UTraceRuntimeError("duplicate_runtime_or_hook", "this worker already holds a uTrace capability", {
        runtime_id: this.#runtimeId,
      });
    }
    this.#ordinal += 1;
    const capability: WorkerCapability = {
      kind: WORKER_CAPABILITY_KIND,
      schema_version: this.#schemaVersion,
      capability_id: opaqueId("wcap", this.#ordinal, this.#random),
      runtime_id: this.#runtimeId,
      document_attachment_id: this.#documentAttachmentId,
      expires_at: expiresAt,
    };
    const channel = new MessageChannel();
    this.#granted.add(worker);
    this.#ports.set(capability.capability_id, channel.port1);
    channel.port1.start();
    worker.postMessage(capability, [channel.port2]);
    return capability;
  }

  portFor(capabilityId: string): MessagePort | null {
    return this.#ports.get(capabilityId) ?? null;
  }

  revokeAll(): void {
    for (const port of this.#ports.values()) port.close();
    this.#ports.clear();
  }
}
