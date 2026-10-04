import { createServer, type Server } from "node:http";
import { once } from "node:events";

import {
  verifyUTraceRouteContext,
  type VerifiedRouteContext,
} from "./route-context-verifier";

/**
 * A real HTTP server standing in for a Vercel Sandbox preview runtime.
 *
 * It behaves like the client product's own middleware: it verifies the door's
 * `x-utrace-route-context` header with the mirrored runtime verifier and refuses the
 * request when the token is missing, forged, or expired. Every
 * request it accepts is recorded so the tests can prove exactly which headers the door
 * forwarded, stripped, and added.
 */

export interface ReceivedRuntimeRequest {
  readonly method: string;
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
  readonly claims: VerifiedRouteContext;
}

export interface RuntimeResponse {
  readonly status: number;
  readonly headers: Readonly<Record<string, string | string[]>>;
  readonly body: string;
}

export interface RuntimeTestServer {
  readonly origin: string;
  readonly received: ReceivedRuntimeRequest[];
  readonly rejected: { count: number };
  /** The answer the runtime returns to an authorised request. */
  respond: (request: ReceivedRuntimeRequest) => RuntimeResponse;
  close: () => Promise<void>;
}

export async function startRuntimeServer(params: {
  port: number;
  signingSecret: string;
}): Promise<RuntimeTestServer> {
  const received: ReceivedRuntimeRequest[] = [];
  const rejected = { count: 0 };
  const state = {
    respond: (request: ReceivedRuntimeRequest): RuntimeResponse => ({
      status: 200,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify({
        product: "client preview runtime",
        path: request.url,
        preview_version_id: request.claims.preview_version_id,
      }),
    }),
  };

  const server: Server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      void (async () => {
        const headers: Record<string, string> = {};
        for (const [name, value] of Object.entries(request.headers)) {
          if (typeof value === "string") {
            headers[name] = value;
          } else if (Array.isArray(value)) {
            headers[name] = value.join(", ");
          }
        }
        const token = headers["x-utrace-route-context"];
        if (token === undefined) {
          rejected.count += 1;
          response.writeHead(401, {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
          });
          response.end(JSON.stringify({ state: "unavailable" }));
          return;
        }
        let claims: VerifiedRouteContext;
        try {
          claims = await verifyUTraceRouteContext(token, params.signingSecret, new Date());
        } catch {
          rejected.count += 1;
          response.writeHead(401, {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
          });
          response.end(JSON.stringify({ state: "unavailable" }));
          return;
        }
        const entry: ReceivedRuntimeRequest = {
          method: request.method ?? "GET",
          url: request.url ?? "/",
          headers,
          body: Buffer.concat(chunks).toString("utf8"),
          claims,
        };
        received.push(entry);
        const answer = state.respond(entry);
        response.writeHead(answer.status, {
          ...answer.headers,
        });
        response.end(answer.body);
      })();
    });
  });

  server.listen(params.port, "127.0.0.1");
  await once(server, "listening");

  return {
    origin: `http://127.0.0.1:${params.port}`,
    received,
    rejected,
    get respond() {
      return state.respond;
    },
    set respond(value: (request: ReceivedRuntimeRequest) => RuntimeResponse) {
      state.respond = value;
    },
    close: async () => {
      server.close();
      await once(server, "close");
    },
  };
}
