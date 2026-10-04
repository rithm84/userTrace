/**
 * The instrumented child frame runtime.
 *
 * A child frame authenticates its own document and sends normalized records to
 * the top-document coordinator over an authenticated `MessageChannel`. It never
 * exports: it obtains no telemetry credential, opens no ingestion WebSocket,
 * assigns no `export_sequence`, and holds no buffer. The coordinator
 * re-envelopes what it sends and remains the page's only exporter.
 *
 * An uninstrumented frame is simply opaque to observation. Nothing here makes a
 * frame observable without its own verified attestation.
 */

import type { BrowserSdkConfig } from "../core/config.js";
import type { BrowserView } from "../core/dom.js";
import { UTraceRuntimeError } from "../core/failure.js";
import type { RandomSource } from "../core/ids.js";
import { uuidv7, webCryptoRandom } from "../core/ids.js";
import type { Clock } from "../core/time.js";
import { documentClock } from "../core/time.js";
import { MAX_OBSERVATION_BYTES } from "../core/limits.js";
import type { AttestationResult } from "../activation/gate.js";
import { attestDocument, controlFetch } from "../activation/gate.js";
import { asObject, parseJson } from "../core/json.js";
import type { ExportRecord } from "../export/record.js";
import { ObservationCore } from "./core.js";
import { connectToCoordinator, FRAME_ACCEPT_KIND, FRAME_RECORD_KIND } from "./frames.js";

/** How long the child waits for the coordinator to accept its attachment. */
const ACCEPT_TIMEOUT_MS = 10_000;

/**
 * How often the child re-offers itself. A child document can finish attesting
 * before the top document's coordinator is listening, so the offer repeats
 * until it is accepted or the bound elapses. The coordinator keys attachments
 * by `frame_id`, so a repeated offer replaces the earlier one rather than
 * creating a second attachment.
 */
const OFFER_INTERVAL_MS = 200;

export class FrameObservationRuntime {
  readonly view: BrowserView;
  readonly clock: Clock;
  readonly random: RandomSource;
  readonly #config: BrowserSdkConfig;
  readonly #runtimeId: string;
  readonly #frameId: string;
  #core: ObservationCore | null = null;
  #port: MessagePort | null = null;
  #attached = false;

  constructor(view: BrowserView, config: BrowserSdkConfig) {
    this.view = view;
    this.clock = documentClock(view);
    this.random = webCryptoRandom(view.crypto);
    this.#config = config;
    this.#runtimeId = uuidv7(this.clock.wallNowMs(), this.random);
    this.#frameId = uuidv7(this.clock.wallNowMs(), this.random);
  }

  get runtimeId(): string {
    return this.#runtimeId;
  }

  get frameId(): string {
    return this.#frameId;
  }

  get attached(): boolean {
    return this.#attached;
  }

  /**
   * Attests this document, joins the coordinator, installs the capture hooks,
   * and forwards a baseline. Rejects if the coordinator does not accept.
   */
  async start(): Promise<void> {
    if (this.view.top === this.view) {
      throw new UTraceRuntimeError("required_frame_or_adapter_missing", "this document is not a child frame", {
        frame_id: this.#frameId,
      });
    }
    const attestation = await attestDocument({
      config: this.#config,
      documentOrigin: this.view.location.origin,
      fetchControl: controlFetch(this.view),
      subtle: this.view.crypto.subtle,
      random: this.random,
      nowMs: () => this.clock.wallNowMs(),
    });
    await this.#join(attestation);
    const core = this.#buildCore(attestation);
    core.installHooks();
    core.seedRegistry();
    await core.emitBaseline("activation", MAX_OBSERVATION_BYTES);
    core.streamRegistryChanges();
    core.startPeriodicWork();
  }

  #buildCore(attestation: AttestationResult): ObservationCore {
    const core = new ObservationCore(
      this.view,
      this.clock,
      this.random,
      this.#config,
      attestation,
      {
        runtime_id: this.#runtimeId,
        document_attachment_id: attestation.attestation.document_attachment_id,
        frame_id: this.#frameId,
      },
      {
        deliver: (record: ExportRecord): void => {
          this.#forward(record);
        },
      },
      {
        onRouteCommitted: (): void => {
          void core.emitBaseline("settled_route_transition", MAX_OBSERVATION_BYTES);
        },
        onRecordDelivered: (): void => {
          // The coordinator owns batching and flow control; a child posts each
          // record as it is produced.
        },
      },
    );
    this.#core = core;
    return core;
  }

  async #join(attestation: AttestationResult): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      let offered: MessagePort | null = null;
      const deadline = this.clock.wallNowMs() + ACCEPT_TIMEOUT_MS;

      const onMessage = (event: MessageEvent): void => {
        const message = asObject(parseJson(String(event.data), "frame_accept"), "frame_accept");
        if (message["kind"] !== FRAME_ACCEPT_KIND) return;
        this.view.clearInterval(interval);
        this.#attached = true;
        resolve();
      };

      const offer = (): void => {
        if (this.#attached) return;
        if (this.clock.wallNowMs() > deadline) {
          this.view.clearInterval(interval);
          offered?.close();
          reject(
            new UTraceRuntimeError("required_frame_or_adapter_missing", "the coordinator did not accept this frame", {
              frame_id: this.#frameId,
              timeout_ms: ACCEPT_TIMEOUT_MS,
            }),
          );
          return;
        }
        offered?.close();
        const port = connectToCoordinator(
          this.view,
          this.#frameId,
          this.#runtimeId,
          this.#config.schemaVersion,
          attestation.attestationEnvelope,
        );
        port.addEventListener("message", onMessage);
        offered = port;
        this.#port = port;
      };

      const interval = this.view.setInterval(offer, OFFER_INTERVAL_MS);
      offer();
    });
  }

  /** Posts one normalized record to the coordinator. The child never exports. */
  #forward(record: ExportRecord): void {
    if (!this.#attached || this.#port === null) return;
    this.#port.postMessage(
      JSON.stringify({
        kind: FRAME_RECORD_KIND,
        record: {
          observation_id: record.envelope.observation_id,
          observation_type: record.envelope.observation_type,
          source_sequence: record.envelope.source_sequence,
          occurred_at: record.envelope.occurred_at,
          monotonic_offset: record.envelope.monotonic_offset,
          route_revision: record.envelope.route_revision,
          visibility_state: record.envelope.visibility_state,
          body: record.body,
        },
      }),
    );
  }

  terminate(): void {
    this.#core?.setSuspended(true);
    this.#core?.teardown();
    this.#port?.close();
    this.#port = null;
    this.#attached = false;
  }
}
