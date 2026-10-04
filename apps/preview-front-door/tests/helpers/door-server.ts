import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { once } from "node:events";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

import {
  handleClaimRequest,
  handleProxyRequest,
  handleSignInRequest,
} from "@/lib/door";
import {
  handleActivationRelay,
  handlePropagationProbe,
  handleTelemetryCredentialRelay,
} from "@/lib/relays";

/**
 * The door's route handlers, served over a real socket.
 *
 * The handlers are plain Web `Request` to `Response` functions, which is exactly how the
 * App Router invokes them, so this adapter exercises the shipped code rather than a copy.
 * The dispatch below mirrors the route table `next build` prints: the four reserved
 * `/__utrace/*` segments win over the optional catch-all that serves everything else.
 */

const bodilessMethods: ReadonlySet<string> = new Set(["GET", "HEAD"]);

export interface DoorTestServer {
  readonly port: number;
  close: () => Promise<void>;
}

async function toWebRequest(request: IncomingMessage): Promise<Request> {
  const host = request.headers.host ?? "door.invalid";
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    if (typeof value === "string") {
      headers.set(name, value);
    } else if (Array.isArray(value)) {
      for (const item of value) {
        headers.append(name, item);
      }
    }
  }
  const method = (request.method ?? "GET").toUpperCase();
  const init: RequestInit & { duplex: "half" } = {
    method,
    headers,
    body: bodilessMethods.has(method)
      ? null
      : (Readable.toWeb(request) as ReadableStream<Uint8Array>),
    duplex: "half",
  };
  return new Request(`http://${host}${request.url ?? "/"}`, init);
}

async function dispatch(request: Request): Promise<Response> {
  const pathname = new URL(request.url).pathname;
  if (pathname === "/__utrace/claim") {
    return request.method === "GET"
      ? handleClaimRequest(request)
      : new Response(null, { status: 405 });
  }
  if (pathname === "/__utrace/sign-in") {
    return request.method === "GET"
      ? handleSignInRequest(request)
      : new Response(null, { status: 405 });
  }
  if (pathname === "/__utrace/activate") {
    return request.method === "POST"
      ? handleActivationRelay(request)
      : new Response(null, { status: 405 });
  }
  if (pathname === "/__utrace/telemetry-credential") {
    return request.method === "POST"
      ? handleTelemetryCredentialRelay(request)
      : new Response(null, { status: 405 });
  }
  if (pathname === "/__utrace/propagation-probe") {
    return request.method === "POST"
      ? handlePropagationProbe(request)
      : new Response(null, { status: 405 });
  }
  return handleProxyRequest(request);
}

async function writeResponse(
  response: Response,
  target: ServerResponse,
): Promise<void> {
  const setCookies = response.headers.getSetCookie();
  for (const [name, value] of response.headers) {
    if (name !== "set-cookie") {
      target.setHeader(name, value);
    }
  }
  if (setCookies.length > 0) {
    target.setHeader("set-cookie", setCookies);
  }
  target.writeHead(response.status);
  if (response.body === null) {
    target.end();
    return;
  }
  await pipeline(
    Readable.fromWeb(response.body as import("node:stream/web").ReadableStream),
    target,
  );
}

export async function startDoorServer(params: {
  port: number;
}): Promise<DoorTestServer> {
  const server: Server = createServer((request, response) => {
    void (async () => {
      const webRequest = await toWebRequest(request);
      const answer = await dispatch(webRequest);
      await writeResponse(answer, response);
    })();
  });
  server.listen(params.port, "127.0.0.1");
  await once(server, "listening");
  return {
    port: params.port,
    close: async () => {
      server.close();
      await once(server, "close");
    },
  };
}
