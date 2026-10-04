/**
 * Extension bridge endpoint.
 *
 * The extension opens the channel: it posts a `page_proof` request on `window`
 * with one `MessagePort` transferred, and the SDK answers the proof on that
 * port and then serves `target_query`, `detail_query`, and `pause_notice` on
 * it. The service worker's page-proof probe and the content script's long-lived
 * bridge use the same handshake, so a probe simply takes the answer and closes
 * its port.
 *
 * The SDK owns structured target resolution, geometry, and the signed page
 * proof; the extension owns binding, controls, character state, and visual
 * commands. No credential crosses this port, and product telemetry never
 * travels over it.
 */

import type { JsonObject, JsonValue } from "../core/json.js";
import type { SharedObservationRuntime } from "../runtime/runtime.js";
import { measureGeometry } from "../registry/geometry.js";
import type { TargetSummary } from "../observation/types.js";
import { evaluateDetailQuery } from "./detail-query.js";
import type { BridgeFrameKind, BridgeRequest, BridgeTargetQuery } from "./messages.js";
import { bridgeFrame, decodeBridgeFrame } from "./messages.js";

/** Matches the extension protocol's `maxItems` on every resolved-target array. */
const MAX_RESOLVED_TARGETS = 12;

export interface BridgeCallbacks {
  /**
   * The extension reports an authoritative session control transition. The
   * shared runtime follows it; it never decides the transition itself.
   */
  readonly onPauseNotice: (state: "paused" | "running" | "ended") => Promise<void>;
  readonly onRejected: (reason: string) => void;
}

/** One resolved target, in the shape the extension's scene geometry consumes. */
interface ResolvedTarget extends JsonObject {
  readonly target_id: string;
  readonly target_revision: number;
  readonly layout_revision: number;
  readonly frame_id: string;
  readonly rect: { readonly x: number; readonly y: number; readonly width: number; readonly height: number };
  readonly visible_fraction: number;
  readonly occluded: boolean;
  readonly in_viewport: boolean;
  readonly supports_visual_guidance: boolean;
}

export class ExtensionBridge {
  readonly #runtime: SharedObservationRuntime;
  readonly #callbacks: BridgeCallbacks;
  #port: MessagePort | null = null;
  #discoveryListener: ((event: MessageEvent) => void) | null = null;

  constructor(runtime: SharedObservationRuntime, callbacks: BridgeCallbacks) {
    this.#runtime = runtime;
    this.#callbacks = callbacks;
  }

  get connected(): boolean {
    return this.#port !== null;
  }

  /** Starts listening for the extension's page-proof request. */
  listen(): () => void {
    const view = this.#runtime.view;
    const listener = (event: MessageEvent): void => {
      if (event.origin !== view.location.origin || event.source !== view) return;
      const port = event.ports[0];
      if (port === undefined) return;
      let request: BridgeRequest;
      try {
        request = decodeBridgeFrame(event.data as JsonValue);
      } catch {
        // A page may post anything it likes on its own window. Only a frame
        // that decodes as a page-proof request is ours; the rest is not an
        // error and must not be reported as a rejected bridge message.
        return;
      }
      if (request.kind !== "page_proof") {
        this.#callbacks.onRejected(`the bridge handshake carried ${request.kind}, not page_proof`);
        return;
      }
      this.#accept(port, request.exchange_id);
    };
    this.#discoveryListener = listener;
    view.addEventListener("message", listener);
    return (): void => {
      this.close();
    };
  }

  /**
   * Takes the transferred port and answers the proof on it. A later handshake
   * replaces an earlier one: the service worker probes before the content
   * script attaches, and a reinjected content script attaches again.
   */
  #accept(port: MessagePort, exchangeId: string): void {
    this.#port?.close();
    this.#port = port;
    port.addEventListener("message", (event: MessageEvent) => {
      void this.#onMessage(event);
    });
    port.start();
    this.#sendPageProof(port, exchangeId);
  }

  #sendPageProof(port: MessagePort, exchangeId: string): void {
    const proof = this.#runtime.pageProof;
    if (proof === null) {
      this.#callbacks.onRejected("no page proof is available before activation");
      return;
    }
    port.postMessage(
      bridgeFrame("page_proof", exchangeId, {
        page_proof: { ...proof },
        document_attachment_id: this.#runtime.attestationDocumentAttachmentId,
        registry_revision: this.#runtime.registry.registryRevision,
      }),
    );
  }

  async #onMessage(event: MessageEvent): Promise<void> {
    const receivedAtMs = this.#runtime.clock.wallNowMs();
    let request: BridgeRequest;
    try {
      request = decodeBridgeFrame(event.data as JsonValue);
    } catch (cause) {
      this.#callbacks.onRejected(`undecodable bridge frame: ${String(cause)}`);
      return;
    }
    switch (request.kind) {
      case "page_proof": {
        const port = this.#port;
        if (port !== null) this.#sendPageProof(port, request.exchange_id);
        return;
      }
      case "target_query":
        this.#send("target_geometry", request.exchange_id, this.#resolveQuery(request.query));
        return;
      case "detail_query": {
        const result = evaluateDetailQuery(
          this.#runtime,
          {
            document_attachment_id: request.document_attachment_id,
            expected_registry_revision: request.expected_registry_revision,
            target_id: request.target_id,
            fields: request.fields,
            depth_bound: request.depth_limit,
            size_bound: request.size_limit_bytes,
            deadline_ms: request.deadline_ms,
          },
          this.#runtime.clock.wallNowMs(),
          receivedAtMs,
        );
        this.#send(
          "detail_query",
          request.exchange_id,
          result.status === "ok"
            ? {
                disposition: "resolved",
                target_revision: this.#revisionOf(request.target_id),
                fields: result.fields,
              }
            : { disposition: "failed", reason: result.status },
        );
        return;
      }
      case "pause_notice":
        await this.#callbacks.onPauseNotice(request.pause_state);
    }
  }

  /** The current revision of a target the detail query already resolved. */
  #revisionOf(targetId: string): number {
    const resolved = this.#runtime.registry.byId(targetId);
    return resolved.status === "found" ? resolved.target.target_revision : 0;
  }

  /**
   * Resolves one structured query into clipped top-level viewport geometry.
   * Zero matches are `not_found` and several are `ambiguous`; the runtime never
   * picks a nearest guess.
   */
  #resolveQuery(query: BridgeTargetQuery): JsonObject {
    const view = this.#runtime.view;
    const targets: ResolvedTarget[] = [];
    const notFound: string[] = [];
    const ambiguous: string[] = [];

    if (query.by === "target_ids") {
      for (const targetId of query.target_ids.slice(0, MAX_RESOLVED_TARGETS)) {
        const resolved = this.#runtime.registry.byId(targetId);
        if (resolved.status === "found") targets.push(this.#resolveTarget(resolved.target));
        else if (resolved.status === "ambiguous") ambiguous.push(targetId);
        else notFound.push(targetId);
      }
    } else {
      const resolved = this.#runtime.registry.query(
        {
          definition_id: query.definition_id,
          ...(query.effect === "any" ? {} : { effect: query.effect }),
          ...(query.route_template === null ? {} : { route_template: query.route_template }),
          visibility: query.visibility,
          ...(query.entity_ref === null
            ? {}
            : { entity_ref: { entity_definition_id: query.definition_id, instance_ref: query.entity_ref } }),
        },
        this.#runtime.currentRoute().route_template,
      );
      if (resolved.status === "found") targets.push(this.#resolveTarget(resolved.target));
      else if (resolved.status === "ambiguous") ambiguous.push(query.definition_id);
      else if (resolved.status === "stale_target") notFound.push(resolved.target_id);
      else notFound.push(query.definition_id);
    }

    return {
      document_attachment_id: this.#runtime.attestationDocumentAttachmentId,
      registry_revision: this.#runtime.registry.registryRevision,
      viewport: { width: view.innerWidth, height: view.innerHeight },
      targets,
      not_found: notFound,
      ambiguous,
    };
  }

  /**
   * Re-measures the target so the answer reflects the layout at the moment the
   * extension asked, including occlusion and the fraction of the element that
   * is actually on screen.
   */
  #resolveTarget(target: TargetSummary): ResolvedTarget {
    const element = this.#runtime.registry.elementFor(target.target_id);
    const geometry = element === null ? target.geometry : measureGeometry(element, this.#runtime.view);
    return {
      target_id: target.target_id,
      target_revision: target.target_revision,
      layout_revision: target.layout_revision,
      frame_id: this.#runtime.runtimeId,
      rect: { x: geometry.x, y: geometry.y, width: geometry.width, height: geometry.height },
      visible_fraction: geometry.visible_fraction,
      occluded: geometry.occluded,
      in_viewport: geometry.visibility === "visible",
      supports_visual_guidance: !target.semantic_state.disabled,
    };
  }

  #send(kind: BridgeFrameKind, exchangeId: string, payload: JsonObject): void {
    this.#port?.postMessage(bridgeFrame(kind, exchangeId, payload));
  }

  close(): void {
    this.#port?.close();
    this.#port = null;
    if (this.#discoveryListener !== null) {
      this.#runtime.view.removeEventListener("message", this.#discoveryListener);
      this.#discoveryListener = null;
    }
  }
}
