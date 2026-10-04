import { createServer, type Server } from "node:http";
import { once } from "node:events";

import {
  activationRelayRequestSchema,
  handoffExchangeRequestSchema,
  telemetryCredentialRelayRequestSchema,
} from "@/lib/contracts";

/**
 * A real HTTP server implementing the control plane's documented preview routes.
 *
 * The control plane itself is built in another workstream, so these tests stand in for it
 * with a server that speaks the same four endpoints, the same bearer service token, the
 * same `x-utrace-session-credential` header, and the same typed denial bodies. Nothing is
 * mocked: the door reaches this server over a socket in the 8600-8699 range.
 */

export interface ScriptedAnswer {
  readonly status: number;
  readonly body: unknown;
}

export interface ReceivedControlPlaneRequest {
  readonly method: string;
  readonly url: string;
  readonly authorization: string | null;
  readonly sessionCredential: string | null;
  readonly body: unknown;
}

export interface ControlPlaneTestServer {
  readonly baseUrl: string;
  readonly received: ReceivedControlPlaneRequest[];
  handoffs: Map<string, ScriptedAnswer>;
  routes: Map<string, ScriptedAnswer>;
  productSignIn: ScriptedAnswer;
  activation: ScriptedAnswer;
  telemetryCredential: ScriptedAnswer;
  validSessionCredentials: Set<string>;
  /** Answers this many requests with 503 before behaving normally again. */
  transientFailures: number;
  close: () => Promise<void>;
}

const denial = (reason: string): ScriptedAnswer["body"] => ({
  schema_version: "utrace_preview_denial_v1",
  reason,
});

export async function startControlPlaneServer(params: {
  port: number;
  serviceToken: string;
}): Promise<ControlPlaneTestServer> {
  const state = {
    received: [] as ReceivedControlPlaneRequest[],
    handoffs: new Map<string, ScriptedAnswer>(),
    routes: new Map<string, ScriptedAnswer>(),
    productSignIn: { status: 404, body: denial("unknown_host") },
    activation: { status: 404, body: denial("activation_rejected") },
    telemetryCredential: { status: 404, body: denial("activation_rejected") },
    validSessionCredentials: new Set<string>(),
    transientFailures: 0,
  };

  const server: Server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      const body: unknown = raw.length === 0 ? null : JSON.parse(raw);
      const url = request.url ?? "/";
      const authorization = request.headers.authorization ?? null;
      const sessionCredential =
        typeof request.headers["x-utrace-session-credential"] === "string"
          ? request.headers["x-utrace-session-credential"]
          : null;
      state.received.push({
        method: request.method ?? "GET",
        url,
        authorization,
        sessionCredential,
        body,
      });

      const answer = route({
        method: request.method ?? "GET",
        url,
        authorization,
        sessionCredential,
        body,
        state,
        serviceToken: params.serviceToken,
      });
      response.writeHead(answer.status, {
        "content-type": "application/json; charset=utf-8",
      });
      response.end(JSON.stringify(answer.body));
    });
  });

  server.listen(params.port, "127.0.0.1");
  await once(server, "listening");

  return {
    baseUrl: `http://127.0.0.1:${params.port}`,
    get received() {
      return state.received;
    },
    get handoffs() {
      return state.handoffs;
    },
    get routes() {
      return state.routes;
    },
    get productSignIn() {
      return state.productSignIn;
    },
    set productSignIn(value: ScriptedAnswer) {
      state.productSignIn = value;
    },
    get activation() {
      return state.activation;
    },
    set activation(value: ScriptedAnswer) {
      state.activation = value;
    },
    get telemetryCredential() {
      return state.telemetryCredential;
    },
    set telemetryCredential(value: ScriptedAnswer) {
      state.telemetryCredential = value;
    },
    get validSessionCredentials() {
      return state.validSessionCredentials;
    },
    get transientFailures() {
      return state.transientFailures;
    },
    set transientFailures(value: number) {
      state.transientFailures = value;
    },
    close: async () => {
      server.close();
      await once(server, "close");
    },
  };
}

interface RouteInput {
  readonly method: string;
  readonly url: string;
  readonly authorization: string | null;
  readonly sessionCredential: string | null;
  readonly body: unknown;
  readonly serviceToken: string;
  readonly state: {
    handoffs: Map<string, ScriptedAnswer>;
    routes: Map<string, ScriptedAnswer>;
    productSignIn: ScriptedAnswer;
    activation: ScriptedAnswer;
    telemetryCredential: ScriptedAnswer;
    validSessionCredentials: Set<string>;
    transientFailures: number;
  };
}

function route(input: RouteInput): ScriptedAnswer {
  if (input.state.transientFailures > 0) {
    input.state.transientFailures -= 1;
    return { status: 503, body: { error: "control plane is restarting" } };
  }
  if (input.authorization !== `Bearer ${input.serviceToken}`) {
    return { status: 401, body: { error: "service token rejected" } };
  }

  const exchangeMatch = /^\/v1\/preview\/handoffs\/([^/]+)\/exchange$/.exec(
    input.url,
  );
  if (exchangeMatch !== null) {
    const parsed = handoffExchangeRequestSchema.safeParse(input.body);
    if (!parsed.success) {
      return { status: 422, body: { error: "exchange request body rejected" } };
    }
    const code = decodeURIComponent(exchangeMatch[1] as string);
    return (
      input.state.handoffs.get(code) ?? {
        status: 404,
        body: denial("unknown_handoff_code"),
      }
    );
  }

  const routeMatch = /^\/v1\/preview\/routes\/([^/?]+)$/.exec(input.url);
  if (routeMatch !== null) {
    const host = decodeURIComponent(routeMatch[1] as string);
    const answer = input.state.routes.get(host);
    if (answer === undefined) {
      return { status: 404, body: denial("unknown_host") };
    }
    if (
      input.sessionCredential === null ||
      !input.state.validSessionCredentials.has(input.sessionCredential)
    ) {
      return { status: 403, body: denial("unauthorized_session") };
    }
    return answer;
  }

  if (/^\/v1\/preview\/routes\/[^/?]+\/product-sign-in$/.test(input.url)) {
    if (
      input.method !== "POST" ||
      input.sessionCredential === null ||
      !input.state.validSessionCredentials.has(input.sessionCredential)
    ) {
      return { status: 403, body: denial("unauthorized_session") };
    }
    return input.state.productSignIn;
  }

  if (input.url === "/v1/preview/activations") {
    const parsed = activationRelayRequestSchema.safeParse(input.body);
    if (!parsed.success) {
      return { status: 422, body: { error: "activation body rejected" } };
    }
    if (
      input.sessionCredential === null ||
      !input.state.validSessionCredentials.has(input.sessionCredential)
    ) {
      return { status: 403, body: denial("unauthorized_session") };
    }
    return input.state.activation;
  }

  if (input.url === "/v1/preview/telemetry-credentials") {
    const parsed = telemetryCredentialRelayRequestSchema.safeParse(input.body);
    if (!parsed.success) {
      return {
        status: 422,
        body: { error: "telemetry credential body rejected" },
      };
    }
    if (
      input.sessionCredential === null ||
      !input.state.validSessionCredentials.has(input.sessionCredential)
    ) {
      return { status: 403, body: denial("unauthorized_session") };
    }
    return input.state.telemetryCredential;
  }

  return { status: 404, body: { error: "no such control plane route" } };
}
