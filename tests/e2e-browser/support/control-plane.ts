/**
 * The one test control plane every component in the journey talks to.
 *
 * The real control plane is built by other workstreams. This server implements
 * the documented contracts the four components under test depend on, so the
 * journey exercises real code on both sides of every seam:
 *
 * | Path | Who calls it | Contract |
 * | --- | --- | --- |
 * | `POST /v1/extension/installations` | the extension | `installation-registration.v1` |
 * | `WS /v1/extension/control` | the extension | `extension-protocol/inbound-frame.v1` and `outbound-frame.v1` |
 * | `POST /v1/preview/handoffs/{code}/exchange` | the front door | `preview/handoff-exchange.v1` |
 * | `GET /v1/preview/routes/{host}` | the front door | `preview/route-record.v1` |
 * | `POST /v1/preview/routes/{host}/product-sign-in` | the front door | `preview/product-sign-in.v1` |
 * | `POST /v1/preview/activations` | the front door | `preview/activation.v1` |
 * | `POST /v1/preview/telemetry-credentials` | the front door | `preview/telemetry-credential.v1` |
 *
 * Browser telemetry ingestion, the Server SDK's control socket, and the OTLP
 * edge are not reimplemented here: the journey runs the very servers the
 * browser SDK and the Server SDK workstreams wrote against their own
 * contracts, so a disagreement fails their suites too rather than only this
 * one.
 *
 * It holds no product logic and decides nothing: it answers with the records
 * the journey pinned, so a failure is always the component's, never a
 * negotiation between two test doubles.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";

import { WebSocketServer, type WebSocket } from "ws";

import { signHandoffToken } from "./handoff.js";
import {
  ALLOCATION_ID,
  CLIENT_ID,
  CLIENT_USER_ID,
  CUSTOMER_ACCOUNT_ID,
  GROWTH_LEAD_EMAIL,
  INSTALLATION_ID,
  PREVIEW_ENVIRONMENT_ID,
  PREVIEW_ROUTE_ID,
  PREVIEW_VERSION_ID,
  PRODUCT_SIGNAL_ID,
  SDK_INSTALLATION_ID,
  SESSION_ID,
  secrets,
  type SignedEnvelope,
  type SigningIdentity,
} from "./identity.js";

/** One frame the extension sent on its control socket. */
export interface ExtensionFrame {
  readonly kind: string;
  readonly payload: Record<string, unknown>;
}

/** The runtime the session origin currently routes to. */
export interface RoutedRuntime {
  readonly allocationId: string;
  readonly previewVersionId: string;
  readonly routeRevision: number;
  /** The secret the runtime verifies its product sign-in token with. */
  readonly handoffSecret: string;
}

export interface ControlPlane {
  readonly origin: string;
  /** Advances the session origin to another runtime, as a ready candidate does. */
  readonly advance: (runtime: RoutedRuntime) => void;
  readonly extensionFrames: readonly ExtensionFrame[];
  readonly activationCount: () => number;
  /** Sends one inbound frame to the connected extension. */
  readonly sendToExtension: (frame: Record<string, unknown>) => void;
  /** Resolves with the first extension frame of `kind` matching `predicate`. */
  readonly waitForExtensionFrame: (
    kind: string,
    predicate: (payload: Record<string, unknown>) => boolean,
  ) => Promise<ExtensionFrame>;
  readonly close: () => Promise<void>;
}

export interface ControlPlaneOptions {
  readonly port: number;
  /** The private half of the browser SDK's packaged trust anchor. */
  readonly signing: SigningIdentity;
  /** The compiled annotation manifest payload the attestation pins. */
  readonly annotationManifest: object;
  /** The session origin the door serves, without scheme or port. */
  readonly publicPreviewHost: string;
  /** Where the browser SDK's telemetry ingestion edge listens. */
  readonly ingestUrl: string;
  /** The origin the browser reports as its document origin, with scheme and port. */
  readonly documentOrigin: string;
  /** Where the Shopalytics preview runtime listens. */
  readonly upstreamOrigin: string;
  readonly applicationPort: number;
}

const json = (response: ServerResponse, status: number, body: unknown): void => {
  const text = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(text),
    "cache-control": "no-store",
  });
  response.end(text);
};

const readBody = async (request: IncomingMessage): Promise<string> => {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk as Buffer));
  return Buffer.concat(chunks).toString("utf8");
};

/** UUIDv7, which every extension-protocol identifier must be. */
const uuidv7 = (): string => {
  const random = randomUUID().replaceAll("-", "");
  const timestamp = Date.now().toString(16).padStart(12, "0");
  const rest = random.slice(12);
  return [
    timestamp.slice(0, 8),
    timestamp.slice(8, 12),
    `7${rest.slice(1, 4)}`,
    `${"89ab"[Math.floor(Math.random() * 4)] ?? "8"}${rest.slice(5, 8)}`,
    rest.slice(8, 20),
  ].join("-");
};

const nowTimestamp = (): string => `${new Date().toISOString().slice(0, 19)}Z`;

export const startControlPlane = async (options: ControlPlaneOptions): Promise<ControlPlane> => {
  const extensionFrames: ExtensionFrame[] = [];
  const waiters = new Set<(frame: ExtensionFrame) => void>();
  let extensionSocket: WebSocket | null = null;
  let activations = 0;
  let controlSequence = 0;
  let handoffConsumed = false;
  let routed: RoutedRuntime = {
    allocationId: ALLOCATION_ID,
    previewVersionId: PREVIEW_VERSION_ID,
    routeRevision: 1,
    handoffSecret: secrets.previewHandoffSecret,
  };

  const server: Server = createServer((request, response) => {
    void (async (): Promise<void> => {
      const url = new URL(request.url ?? "/", `http://127.0.0.1:${String(options.port)}`);
      const authorized = request.headers.authorization === `Bearer ${secrets.controlPlaneServiceToken}`;

      if (url.pathname === "/v1/extension/installations" && request.method === "POST") {
        await readBody(request);
        json(response, 200, {
          schema_version: 1,
          kind: "installation_registration_response",
          installation_id: INSTALLATION_ID,
          account_link_state: "linked",
          negotiated_protocol_version: "1.0",
          control_socket_path: "/v1/extension/control",
          registered_at: nowTimestamp(),
        });
        return;
      }

      const exchange = /^\/v1\/preview\/handoffs\/([^/]+)\/exchange$/u.exec(url.pathname);
      if (exchange !== null && request.method === "POST") {
        await readBody(request);
        if (!authorized) {
          json(response, 401, { error: "service token rejected" });
          return;
        }
        const code = decodeURIComponent(exchange[1] ?? "");
        if (code !== secrets.handoffCode || handoffConsumed) {
          json(response, 404, {
            schema_version: "utrace_preview_denial_v1",
            reason: handoffConsumed ? "handoff_consumed" : "unknown_handoff_code",
          });
          return;
        }
        handoffConsumed = true;
        json(response, 200, {
          schema_version: "utrace_preview_handoff_exchange_v1",
          public_preview_host: options.publicPreviewHost,
          client_id: CLIENT_ID,
          interview_session_id: SESSION_ID,
          session_credential: {
            value: secrets.sessionCredential,
            expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
        });
        return;
      }

      const signIn = /^\/v1\/preview\/routes\/([^/?]+)\/product-sign-in$/u.exec(url.pathname);
      if (signIn !== null && request.method === "POST") {
        await readBody(request);
        if (!authorized) {
          json(response, 401, { error: "service token rejected" });
          return;
        }
        if (request.headers["x-utrace-session-credential"] !== secrets.sessionCredential) {
          json(response, 403, {
            schema_version: "utrace_preview_denial_v1",
            reason: "unauthorized_session",
          });
          return;
        }
        json(response, 200, {
          schema_version: "utrace_preview_product_sign_in_v1",
          path: "/api/utrace/preview-handoff",
          token: await signHandoffToken({
            subjectEmail: GROWTH_LEAD_EMAIL,
            audience: options.publicPreviewHost,
            secret: routed.handoffSecret,
          }),
        });
        return;
      }

      const route = /^\/v1\/preview\/routes\/([^/?]+)$/u.exec(url.pathname);
      if (route !== null && request.method === "GET") {
        if (!authorized) {
          json(response, 401, { error: "service token rejected" });
          return;
        }
        if (request.headers["x-utrace-session-credential"] !== secrets.sessionCredential) {
          json(response, 403, {
            schema_version: "utrace_preview_denial_v1",
            reason: "unauthorized_session",
          });
          return;
        }
        json(response, 200, {
          schema_version: "utrace_preview_route_record_v1",
          preview_route_id: PREVIEW_ROUTE_ID,
          client_id: CLIENT_ID,
          interview_session_id: SESSION_ID,
          product_signal_id: PRODUCT_SIGNAL_ID,
          preview_version_id: routed.previewVersionId,
          preview_runtime_allocation_id: routed.allocationId,
          public_preview_host: options.publicPreviewHost,
          route_revision: routed.routeRevision,
          readiness_state: "ready",
          upstream: {
            port_url: options.upstreamOrigin,
            application_port: options.applicationPort,
          },
          preview_entry_token: secrets.previewEntryToken,
          route_context_signing_secret: secrets.routeContextSigningSecret,
          expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        });
        return;
      }

      if (url.pathname === "/v1/preview/activations" && request.method === "POST") {
        const body = JSON.parse(await readBody(request)) as {
          activation_request?: { document_nonce?: string };
        };
        if (!authorized) {
          json(response, 401, { error: "service token rejected" });
          return;
        }
        activations += 1;
        json(
          response,
          200,
          activationResponse(options, routed, body.activation_request?.document_nonce ?? ""),
        );
        return;
      }

      if (url.pathname === "/v1/preview/telemetry-credentials" && request.method === "POST") {
        await readBody(request);
        if (!authorized) {
          json(response, 401, { error: "service token rejected" });
          return;
        }
        json(response, 200, {
          credential: {
            token: secrets.telemetryToken,
            expires_at: new Date(Date.now() + 600_000).toISOString(),
            ingest_url: options.ingestUrl,
          },
          limits: {
            max_observation_bytes: 65_536,
            max_batch_bytes: 262_144,
            max_bytes_per_second: 1_000_000,
            max_records_per_second: 500,
            max_in_flight_batches: 4,
            acknowledgement_lag_limit_ms: 5_000,
          },
        });
        return;
      }

      response.writeHead(404, { "content-type": "text/plain" });
      response.end("no such control plane route");
    })();
  });

  const sockets = new WebSocketServer({ noServer: true });
  server.on("upgrade", (request, socket, head) => {
    const pathname = new URL(request.url ?? "/", "http://127.0.0.1").pathname;
    if (pathname === "/v1/extension/control") {
      sockets.handleUpgrade(request, socket, head, (connection) => {
        extensionSocket = connection;
        connection.on("message", (data: Buffer) => {
          const frame = JSON.parse(data.toString("utf8")) as {
            kind: string;
            payload: Record<string, unknown>;
          };
          const received: ExtensionFrame = { kind: frame.kind, payload: frame.payload };
          extensionFrames.push(received);
          for (const waiter of [...waiters]) waiter(received);
        });
      });
      return;
    }
    socket.destroy();
  });

  await new Promise<void>((resolve) => {
    server.listen(options.port, "127.0.0.1", resolve);
  });

  return {
    origin: `http://127.0.0.1:${String(options.port)}`,
    advance: (runtime: RoutedRuntime): void => {
      routed = runtime;
    },
    extensionFrames,
    activationCount: (): number => activations,
    sendToExtension: (frame: Record<string, unknown>): void => {
      if (extensionSocket === null) {
        throw new Error("no extension is connected to the test control plane");
      }
      controlSequence += 1;
      extensionSocket.send(
        JSON.stringify({
          schema_version: 1,
          message_id: uuidv7(),
          sent_at: nowTimestamp(),
          sequence: controlSequence,
          ...frame,
        }),
      );
    },
    waitForExtensionFrame: (kind, predicate): Promise<ExtensionFrame> => {
      const matched = extensionFrames.find(
        (frame) => frame.kind === kind && predicate(frame.payload),
      );
      if (matched !== undefined) return Promise.resolve(matched);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          waiters.delete(waiter);
          reject(
            new Error(
              `no ${kind} frame matched within 30s; received ${extensionFrames
                .map((frame) => frame.kind)
                .join(", ")}`,
            ),
          );
        }, 30_000);
        const waiter = (frame: ExtensionFrame): void => {
          if (frame.kind !== kind || !predicate(frame.payload)) return;
          clearTimeout(timer);
          waiters.delete(waiter);
          resolve(frame);
        };
        waiters.add(waiter);
      });
    },
    close: (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        sockets.close();
        server.closeAllConnections();
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
};

/**
 * The three signed envelopes the browser SDK verifies with its packaged public
 * key before it will install a single hook.
 */
function activationResponse(
  options: ControlPlaneOptions,
  routed: RoutedRuntime,
  documentNonce: string,
): Record<string, SignedEnvelope> {
  const issuedAt = new Date();
  const expiresAt = new Date(issuedAt.getTime() + 15 * 60_000);
  const documentAttachmentId = uuidv7();
  return {
    attestation: options.signing.sign({
      attestation_id: uuidv7(),
      client_id: CLIENT_ID,
      client_user_id: CLIENT_USER_ID,
      customer_account_id: CUSTOMER_ACCOUNT_ID,
      session_id: SESSION_ID,
      preview_environment_id: PREVIEW_ENVIRONMENT_ID,
      preview_runtime_allocation_id: routed.allocationId,
      preview_version_id: routed.previewVersionId,
      document_attachment_id: documentAttachmentId,
      origin: options.documentOrigin,
      document_nonce: documentNonce,
      sdk_installation_id: SDK_INSTALLATION_ID,
      protocol_version: "1",
      schema_version: "1",
      annotation_manifest_version: readManifestVersion(options.annotationManifest),
      annotation_manifest_digest: options.signing.digest(options.annotationManifest),
      redaction_version: "shopalytics-r-1",
      policy_version: "shopalytics-p-1",
      issued_at: issuedAt.toISOString(),
      expires_at: expiresAt.toISOString(),
    }),
    annotation_manifest: options.signing.sign(options.annotationManifest),
    page_proof: options.signing.sign({
      client_id: CLIENT_ID,
      session_id: SESSION_ID,
      preview_environment_id: PREVIEW_ENVIRONMENT_ID,
      preview_runtime_allocation_id: routed.allocationId,
      origin: options.documentOrigin,
      document_attachment_id: documentAttachmentId,
      document_nonce: documentNonce,
      sdk_protocol_version: "1.0",
      issued_at: issuedAt.toISOString(),
      expires_at: expiresAt.toISOString(),
    }),
  };
}

function readManifestVersion(manifest: object): string {
  const version = (manifest as { manifest_version?: unknown }).manifest_version;
  if (typeof version !== "string") {
    throw new TypeError("the compiled annotation manifest carries no manifest_version");
  }
  return version;
}
