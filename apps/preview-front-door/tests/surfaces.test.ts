import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  startControlPlaneServer,
  type ControlPlaneTestServer,
} from "./helpers/control-plane-server";
import { startDoorServer, type DoorTestServer } from "./helpers/door-server";
import { startRuntimeServer, type RuntimeTestServer } from "./helpers/runtime-server";
import { doorRequest } from "./helpers/http-client";
import {
  configureDoor,
  identities,
  previewApex,
  routeContextSigningSecret,
  routeRecord,
  serviceToken,
  sessionCookieHeader,
  sessionCredential,
} from "./helpers/door-fixture";

/**
 * Honest failure surfaces and door logs: what the door shows when it has no authority,
 * when the session is over, when readiness is withdrawn, and when uTrace itself fails —
 * and what it writes down about every request.
 */

const doorPort = 8604;
const controlPlanePort = 8614;
const runtimePort = 8624;
const sessionHost = `s1u2r3f4a5c6.${previewApex}`;

let controlPlane: ControlPlaneTestServer;
let runtime: RuntimeTestServer;
let door: DoorTestServer;

const get = (params: {
  host: string;
  path: string;
  headers: Readonly<Record<string, string>>;
}) =>
  doorRequest({
    port: doorPort,
    hostHeader: params.host,
    method: "GET",
    path: params.path,
    headers: params.headers,
    body: null,
  });

beforeAll(async () => {
  controlPlane = await startControlPlaneServer({
    port: controlPlanePort,
    serviceToken,
  });
  runtime = await startRuntimeServer({
    port: runtimePort,
    signingSecret: routeContextSigningSecret,
  });
  configureDoor({
    controlPlaneBaseUrl: controlPlane.baseUrl,
    applicationPort: runtimePort,
    routeContextTtlSeconds: 30,
    controlPlaneAttempts: 2,
  });
  door = await startDoorServer({ port: doorPort });
});

afterAll(async () => {
  await door.close();
  await runtime.close();
  await controlPlane.close();
});

beforeEach(() => {
  configureDoor({
    controlPlaneBaseUrl: controlPlane.baseUrl,
    applicationPort: runtimePort,
    routeContextTtlSeconds: 30,
    controlPlaneAttempts: 2,
  });
  controlPlane.routes.clear();
  controlPlane.received.length = 0;
  controlPlane.transientFailures = 0;
  controlPlane.validSessionCredentials.clear();
  controlPlane.validSessionCredentials.add(sessionCredential);
  controlPlane.routes.set(sessionHost, {
    status: 200,
    body: routeRecord({
      publicPreviewHost: sessionHost,
      upstreamOrigin: `http://127.0.0.1:${runtimePort}`,
      applicationPort: runtimePort,
      routeRevision: 4,
      readinessState: "ready",
      expiresAt: new Date(Date.now() + 600_000),
    }),
  });
  runtime.received.length = 0;
});

describe("unknown hosts", () => {
  it.each([
    ["the preview apex", previewApex],
    ["a deeper subdomain", `nested.session1234.${previewApex}`],
    ["an unrelated host", "utrace.dev"],
    ["a label that is too short", `abc.${previewApex}`],
  ])("gives %s the neutral interstitial", async (_case, host) => {
    const response = await get({
      host,
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
    });

    expect(response.status).toBe(404);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.body).toContain("Nothing to open here");
    expect(controlPlane.received).toHaveLength(0);
  });
});

describe("interstitials", () => {
  it("says nothing about a session on the neutral page", async () => {
    const response = await get({
      host: sessionHost,
      path: "/dashboard",
      headers: {},
    });

    expect(response.body).toContain("Nothing to open here");
    expect(response.body).toContain(
      "Preview addresses are private to one session",
    );
    expect(response.body).not.toContain(identities.clientId);
    expect(response.body).not.toContain(identities.interviewSessionId);
    expect(response.body).not.toContain("Shopalytics");
    // No comic devices, no external fetches.
    expect(response.body).not.toContain("Bangers");
    expect(response.body).not.toContain("<script");
    expect(response.body).not.toContain("http://");
    expect(response.body).not.toContain("https://");
  });

  it("retries on its own from the reconnecting page", async () => {
    controlPlane.routes.set(sessionHost, {
      status: 200,
      body: routeRecord({
        publicPreviewHost: sessionHost,
        upstreamOrigin: `http://127.0.0.1:${runtimePort}`,
        applicationPort: runtimePort,
        routeRevision: 4,
        readinessState: "reconnecting",
        expiresAt: new Date(Date.now() + 600_000),
      }),
    });

    const response = await get({
      host: sessionHost,
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
    });

    expect(response.body).toContain('http-equiv="refresh"');
    expect(response.headers["retry-after"]).toBe("5");
  });

  it("claims nothing about the session when uTrace itself is at fault", async () => {
    controlPlane.transientFailures = 5;

    const response = await get({
      host: sessionHost,
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
    });

    expect(response.status).toBe(502);
    expect(response.body).toContain("not reachable right now");
    expect(response.body).toContain("fault on the uTrace side");
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(runtime.received).toHaveLength(0);
    // Two attempts, then the last failure is raised.
    expect(controlPlane.received).toHaveLength(2);
  });

  it("recovers when a transient control-plane failure clears within the attempts", async () => {
    controlPlane.transientFailures = 1;

    const response = await get({
      host: sessionHost,
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
    });

    expect(response.status).toBe(200);
    expect(controlPlane.received).toHaveLength(2);
  });

  it("refuses to route when the control plane rejects the door's service token", async () => {
    process.env.UTRACE_CONTROL_PLANE_SERVICE_TOKEN =
      "a-wrong-but-well-formed-service-token";

    const response = await get({
      host: sessionHost,
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
    });

    expect(response.status).toBe(502);
    expect(response.body).toContain("not reachable right now");
    expect(runtime.received).toHaveLength(0);
  });

  it("refuses to route when the door is misconfigured", async () => {
    delete process.env.UTRACE_PREVIEW_APEX;

    const response = await get({
      host: sessionHost,
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
    });

    expect(response.status).toBe(500);
    expect(response.body).toContain("not reachable right now");
    expect(controlPlane.received).toHaveLength(0);
  });
});

describe("door logs", () => {
  it("records the request shape and route identity, and no secrets", async () => {
    const lines: string[] = [];
    const write = vi
      .spyOn(process.stdout, "write")
      .mockImplementation((chunk: string | Uint8Array): boolean => {
        lines.push(typeof chunk === "string" ? chunk : chunk.toString());
        return true;
      });

    try {
      await get({
        host: sessionHost,
        path: "/accounts/dudulemon/views/tiktok-sizing?token=should-not-be-logged",
        headers: { cookie: sessionCookieHeader(sessionCredential) },
      });
    } finally {
      write.mockRestore();
    }

    const entry = lines
      .map((line) => JSON.parse(line) as Record<string, unknown>)
      .find((line) => line["event"] === "preview_door_request");

    expect(entry).toMatchObject({
      event: "preview_door_request",
      method: "GET",
      pathTemplate: "/:seg/:seg/:seg/:seg",
      status: 200,
      outcome: "proxied",
      clientId: identities.clientId,
      interviewSessionId: identities.interviewSessionId,
      previewRuntimeAllocationId: identities.previewRuntimeAllocationId,
      previewVersionId: identities.previewVersionId,
      routeRevision: 4,
    });
    expect(entry?.["latencyMs"]).toBeTypeOf("number");

    const serialised = JSON.stringify(entry);
    expect(serialised).not.toContain(sessionCredential);
    expect(serialised).not.toContain("dudulemon");
    expect(serialised).not.toContain("should-not-be-logged");
    expect(serialised).not.toContain(routeContextSigningSecret);
    expect(serialised).not.toContain(serviceToken);
  });

  it("keeps the reserved door paths readable in the log", async () => {
    const lines: string[] = [];
    const write = vi
      .spyOn(process.stdout, "write")
      .mockImplementation((chunk: string | Uint8Array): boolean => {
        lines.push(typeof chunk === "string" ? chunk : chunk.toString());
        return true;
      });

    try {
      await get({
        host: sessionHost,
        path: "/__utrace/claim?code=hx7Qm2Zt9Lb4Nv6Ks1Jd3Pf0",
        headers: {},
      });
    } finally {
      write.mockRestore();
    }

    const entry = lines
      .map((line) => JSON.parse(line) as Record<string, unknown>)
      .find((line) => line["event"] === "preview_door_request");

    expect(entry?.["pathTemplate"]).toBe("/__utrace/claim");
    expect(JSON.stringify(entry)).not.toContain("hx7Qm2Zt9Lb4Nv6Ks1Jd3Pf0");
  });
});
