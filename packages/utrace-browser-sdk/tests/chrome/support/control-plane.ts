/**
 * Test preview front door and control plane.
 *
 * This implements the counterpart contracts the browser SDK depends on but does
 * not own: the reserved same-origin `/__utrace/activate` and
 * `/__utrace/telemetry-credential` endpoints, the propagation probe echo, and
 * the client product's own API. Those endpoints belong to the preview front
 * door and control plane workstreams; this server implements their documented
 * contract so the SDK can be tested against real behaviour rather than a mock.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { TestSigningKey } from "./signing.js";

export interface EchoedRequest {
  readonly path: string;
  readonly method: string;
  readonly traceparent: string | null;
  readonly tracestate: string | null;
  readonly traceparent_count: number;
}

export interface ControlPlaneOptions {
  readonly port: number;
  readonly fixtureDir: string;
  readonly bundleDir: string;
  readonly ingestUrl: string;
  readonly thirdPartyOrigin: string;
  readonly signing: TestSigningKey;
  readonly manifest: object;
  /** Forces the next activation to answer with an envelope signed by another key. */
  readonly tamperNextAttestation: { value: boolean };
  /** Lifetime of each telemetry credential issued from now on. */
  readonly credentialLifetimeMs: { value: number };
  readonly maxObservationBytes: number;
}

export interface ControlPlane {
  readonly origin: string;
  readonly requests: readonly EchoedRequest[];
  readonly activationCount: () => number;
  readonly close: () => Promise<void>;
}

const readBody = async (request: IncomingMessage): Promise<string> => {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk as Buffer));
  return Buffer.concat(chunks).toString("utf8");
};

const countHeader = (value: string | string[] | undefined): number => {
  if (value === undefined) return 0;
  if (Array.isArray(value)) return value.length;
  return value.split(",").length;
};

const json = (response: ServerResponse, status: number, body: object): void => {
  const text = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(text),
    "cache-control": "no-store",
  });
  response.end(text);
};

export const startControlPlane = async (options: ControlPlaneOptions): Promise<ControlPlane> => {
  const requests: EchoedRequest[] = [];
  let activations = 0;
  const sessionScope = {
    client_id: randomUUID(),
    client_user_id: randomUUID(),
    customer_account_id: randomUUID(),
    session_id: randomUUID(),
    preview_environment_id: randomUUID(),
    preview_runtime_allocation_id: randomUUID(),
    preview_version_id: randomUUID(),
  };

  const server: Server = createServer((request, response) => {
    void (async (): Promise<void> => {
      const url = new URL(request.url ?? "/", `http://localhost:${String(options.port)}`);
      const traceparent = request.headers["traceparent"];
      requests.push({
        path: url.pathname,
        method: request.method ?? "GET",
        traceparent: typeof traceparent === "string" ? traceparent : null,
        tracestate: typeof request.headers["tracestate"] === "string" ? request.headers["tracestate"] : null,
        traceparent_count: countHeader(traceparent),
      });

      if (url.pathname === "/" || url.pathname === "/dashboard/conversions" || url.pathname === "/dashboard/conversions/framed") {
        let html = readFileSync(join(options.fixtureDir, "app.html"), "utf8").replace(
          "<body>",
          `<body data-third-party-origin="${options.thirdPartyOrigin}">`,
        );
        if (url.pathname === "/dashboard/conversions/framed") {
          html = html.replace("</body>", '<iframe id="embedded-report" src="/frame" title="Embedded report"></iframe></body>');
        }
        response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
        response.end(html);
        return;
      }

      if (url.pathname === "/frame") {
        response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
        response.end(readFileSync(join(options.fixtureDir, "frame.html"), "utf8"));
        return;
      }

      if (url.pathname === "/app.js" || url.pathname === "/frame.js") {
        response.writeHead(200, { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-store" });
        response.end(readFileSync(join(options.bundleDir, url.pathname.slice(1)), "utf8"));
        return;
      }

      if (url.pathname === "/__utrace/activate" && request.method === "POST") {
        activations += 1;
        const body = JSON.parse(await readBody(request)) as Record<string, string>;
        const now = Date.now();
        const attestation = {
          attestation_id: randomUUID(),
          ...sessionScope,
          // A fresh attachment per activation, as the control plane issues one
          // per attested document.
          document_attachment_id: randomUUID(),
          origin: `http://localhost:${String(options.port)}`,
          document_nonce: body["document_nonce"] ?? "",
          sdk_installation_id: body["sdk_installation_id"] ?? "",
          protocol_version: "1",
          schema_version: "1",
          annotation_manifest_version: "test-manifest-1",
          annotation_manifest_digest: options.signing.digest(options.manifest),
          redaction_version: "r-1",
          policy_version: "p-1",
          issued_at: new Date(now).toISOString(),
          expires_at: new Date(now + 15 * 60_000).toISOString(),
        };
        const signAttestation = options.tamperNextAttestation.value
          ? options.signing.signWithForeignKey
          : options.signing.sign;
        options.tamperNextAttestation.value = false;
        json(response, 200, {
          attestation: signAttestation(attestation),
          annotation_manifest: options.signing.sign(options.manifest),
          page_proof: options.signing.sign({
            client_id: sessionScope.client_id,
            session_id: sessionScope.session_id,
            preview_environment_id: sessionScope.preview_environment_id,
            preview_runtime_allocation_id: sessionScope.preview_runtime_allocation_id,
            origin: `http://localhost:${String(options.port)}`,
            document_attachment_id: attestation.document_attachment_id,
            document_nonce: body["document_nonce"] ?? "",
            protocol_version: "1",
          }),
        });
        return;
      }

      if (url.pathname === "/__utrace/telemetry-credential" && request.method === "POST") {
        await readBody(request);
        json(response, 200, {
          credential: {
            token: randomUUID().replaceAll("-", ""),
            expires_at: new Date(Date.now() + options.credentialLifetimeMs.value).toISOString(),
            ingest_url: options.ingestUrl,
          },
          limits: {
            max_observation_bytes: options.maxObservationBytes,
            max_batch_bytes: 1048576,
            max_bytes_per_second: 1_000_000,
            max_records_per_second: 500,
            max_in_flight_batches: 4,
            acknowledgement_lag_limit_ms: 5000,
          },
        });
        return;
      }

      if (url.pathname === "/__utrace/propagation-probe") {
        await readBody(request);
        json(response, 200, {
          traceparent: typeof traceparent === "string" ? traceparent : "",
          tracestate: typeof request.headers["tracestate"] === "string" ? request.headers["tracestate"] : "",
          traceparent_count: countHeader(traceparent),
        });
        return;
      }

      if (url.pathname === "/api/echo") {
        await readBody(request);
        json(response, 200, {
          traceparent: typeof traceparent === "string" ? traceparent : null,
          traceparent_count: countHeader(traceparent),
        });
        return;
      }

      response.writeHead(404, { "content-type": "text/plain" });
      response.end("not found");
    })();
  });

  await new Promise<void>((resolve) => {
    server.listen(options.port, "127.0.0.1", resolve);
  });

  return {
    origin: `http://localhost:${String(options.port)}`,
    requests,
    activationCount: (): number => activations,
    close: (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
};

/** A second origin that must never receive uTrace trace context. */
export const startThirdPartyOrigin = async (
  port: number,
): Promise<{ readonly origin: string; readonly requests: readonly EchoedRequest[]; readonly close: () => Promise<void> }> => {
  const requests: EchoedRequest[] = [];
  const server = createServer((request, response) => {
    const traceparent = request.headers["traceparent"];
    requests.push({
      path: request.url ?? "/",
      method: request.method ?? "GET",
      traceparent: typeof traceparent === "string" ? traceparent : null,
      tracestate: typeof request.headers["tracestate"] === "string" ? request.headers["tracestate"] : null,
      traceparent_count: countHeader(traceparent),
    });
    response.writeHead(200, {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
      "cache-control": "no-store",
    });
    response.end(JSON.stringify({ ok: true }));
  });
  await new Promise<void>((resolve) => {
    server.listen(port, "127.0.0.1", resolve);
  });
  return {
    origin: `http://localhost:${String(port)}`,
    requests,
    close: (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
};
