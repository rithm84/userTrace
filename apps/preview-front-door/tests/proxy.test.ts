import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

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
  previewEntryToken,
  routeRecord,
  serviceToken,
  sessionCookieHeader,
  sessionCookieName,
  sessionCredential,
} from "./helpers/door-fixture";

/**
 * The Vercel-to-sandbox request path: cookie verification, one committed route revision,
 * header stripping, signed route context, streamed answers, and fail-closed refusals.
 */

const doorPort = 8602;
const controlPlanePort = 8612;
const runtimePort = 8622;
const sessionHost = `p1r2o3x4y5z6.${previewApex}`;

let controlPlane: ControlPlaneTestServer;
let runtime: RuntimeTestServer;
let door: DoorTestServer;

const readyRoute = (params: {
  host: string;
  routeRevision: number;
}): { status: number; body: unknown } => ({
  status: 200,
  body: routeRecord({
    publicPreviewHost: params.host,
    upstreamOrigin: `http://127.0.0.1:${runtimePort}`,
    applicationPort: runtimePort,
    routeRevision: params.routeRevision,
    readinessState: "ready",
    expiresAt: new Date(Date.now() + 600_000),
  }),
});

const proxied = (params: {
  host: string;
  method: string;
  path: string;
  headers: Readonly<Record<string, string>>;
  body: string | null;
}) =>
  doorRequest({
    port: doorPort,
    hostHeader: params.host,
    method: params.method,
    path: params.path,
    headers: params.headers,
    body: params.body,
  });

beforeAll(async () => {
  controlPlane = await startControlPlaneServer({
    port: controlPlanePort,
    serviceToken,
  });
  runtime = await startRuntimeServer({
    port: runtimePort,
    signingSecret: "Rt7Yh2Qm9Zx4Cb8Nv1Ks6Jd3Lf0Pw5Ag",
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
  controlPlane.routes.clear();
  controlPlane.received.length = 0;
  controlPlane.validSessionCredentials.clear();
  controlPlane.validSessionCredentials.add(sessionCredential);
  runtime.received.length = 0;
  runtime.rejected.count = 0;
});

describe("proxy", () => {
  it("routes a verified request to the runtime with signed route context", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );

    const response = await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard/conversion?range=last-30-days",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.headers["vary"]).toBe("Cookie");
    expect(JSON.parse(response.body)).toMatchObject({
      product: "client preview runtime",
      preview_version_id: identities.previewVersionId,
    });

    expect(runtime.received).toHaveLength(1);
    const forwarded = runtime.received[0];
    expect(forwarded?.url).toBe("/dashboard/conversion?range=last-30-days");
    expect(forwarded?.claims).toMatchObject({
      schema_version: "utrace_preview_route_v1",
      preview_route_id: identities.previewRouteId,
      client_id: identities.clientId,
      product_signal_id: identities.productSignalId,
      preview_version_id: identities.previewVersionId,
      preview_runtime_allocation_id: identities.previewRuntimeAllocationId,
      public_preview_host: sessionHost,
      route_revision: 4,
    });
    // The session origin travels only in the signed claim; the Host header belongs to the
    // provider hop, and the sandbox port proxy replaces any forwarded host.
    expect(forwarded?.headers["x-forwarded-host"]).toBeUndefined();
    expect(forwarded?.headers["x-forwarded-proto"]).toBe("https");
    expect(forwarded?.headers["host"]).toBe(`127.0.0.1:${runtimePort}`);
    // The Server SDK accepts a remote traceparent only when this token matches,
    // so every forwarded request carries the route's activation-scoped one.
    expect(forwarded?.headers["x-utrace-preview-entry"]).toBe(previewEntryToken);
  });

  it("sends a page load signed in to another runtime to sign in again, and back", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );
    const navigate = (runtimeId: string) =>
      proxied({
        host: sessionHost,
        method: "GET",
        path: "/app/conversion?range=last_30_days",
        headers: {
          cookie: `${sessionCookieHeader(sessionCredential)}; ${sessionCookieName}-runtime=${runtimeId}`,
          "sec-fetch-mode": "navigate",
        },
        body: null,
      });

    const stale = await navigate("0192f3c4-c234-7a7c-bf5d-000000000000");
    expect(stale.status).toBe(303);
    expect(stale.headers["location"]).toBe(
      `/__utrace/sign-in?return_to=${encodeURIComponent("/app/conversion?range=last_30_days")}`,
    );
    expect(runtime.received).toHaveLength(0);

    const current = await navigate(identities.previewRuntimeAllocationId);
    expect(current.status).toBe(200);
    expect(runtime.received).toHaveLength(1);
    // The door's own cookies never reach the product.
    expect(runtime.received[0]?.headers["cookie"]).toBeUndefined();
  });

  it("forwards the product's own Next.js assets under their /_next path", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );

    // next.config.ts rewrites `/_next/*` here before the door's own static layer answers it.
    const response = await proxied({
      host: sessionHost,
      method: "GET",
      path: "/__utrace/next/static/chunks/app.js?v=1",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(200);
    expect(runtime.received).toHaveLength(1);
    expect(runtime.received[0]?.url).toBe("/_next/static/chunks/app.js?v=1");
  });

  it("strips caller-supplied provider, route, and forwarding headers", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );

    await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard",
      headers: {
        cookie: `${sessionCookieHeader(sessionCredential)}; shopalytics_preview_session=v1.abc.def`,
        "x-utrace-route-context": "v1.forged.signature",
        "x-utrace-preview-entry": "forged-preview-entry-token",
        "x-utrace-verified-preview-route-id": "forged",
        "x-forwarded-host": "attacker.example",
        "x-forwarded-proto": "http",
        "x-vercel-id": "iad1::abc",
        "user-agent": "vitest",
      },
      body: null,
    });

    expect(runtime.rejected.count).toBe(0);
    const forwarded = runtime.received[0];
    expect(forwarded?.headers["x-utrace-verified-preview-route-id"]).toBeUndefined();
    expect(forwarded?.headers["x-vercel-id"]).toBeUndefined();
    expect(forwarded?.headers["x-forwarded-host"]).toBeUndefined();
    expect(forwarded?.headers["x-forwarded-proto"]).toBe("https");
    expect(forwarded?.headers["x-utrace-route-context"]).not.toBe(
      "v1.forged.signature",
    );
    // A caller cannot forge preview entry: the door replaces it with the token
    // the control plane issued for this route.
    expect(forwarded?.headers["x-utrace-preview-entry"]).toBe(previewEntryToken);
    expect(forwarded?.headers["user-agent"]).toBe("vitest");
    // The product keeps its own cookies and never sees the front door's.
    expect(forwarded?.headers["cookie"]).toBe(
      "shopalytics_preview_session=v1.abc.def",
    );
  });

  it("forwards method and body and streams the runtime's status and headers back", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );
    runtime.respond = () => ({
      status: 201,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "x-product-header": "kept",
        "set-cookie": [
          "shopalytics_preview_session=v1.new.value; Path=/; HttpOnly",
          "shopalytics_view=tiktok-sizing; Path=/",
        ],
      },
      body: JSON.stringify({ saved_view: "TikTok sizing drop-off" }),
    });

    const response = await proxied({
      host: sessionHost,
      method: "POST",
      path: "/api/saved-views",
      headers: {
        cookie: sessionCookieHeader(sessionCredential),
        "content-type": "application/json",
      },
      body: JSON.stringify({ name: "TikTok sizing drop-off" }),
    });

    expect(response.status).toBe(201);
    expect(response.headers["x-product-header"]).toBe("kept");
    expect(response.setCookies).toStrictEqual([
      "shopalytics_preview_session=v1.new.value; Path=/; HttpOnly",
      "shopalytics_view=tiktok-sizing; Path=/",
    ]);
    expect(JSON.parse(response.body)).toStrictEqual({
      saved_view: "TikTok sizing drop-off",
    });
    expect(runtime.received[0]?.method).toBe("POST");
    expect(runtime.received[0]?.body).toBe(
      JSON.stringify({ name: "TikTok sizing drop-off" }),
    );

    runtime.respond = defaultRuntimeResponse;
  });

  it("rewrites a redirect that would hand the browser the provider port URL", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );
    runtime.respond = () => ({
      status: 302,
      headers: {
        location: `http://127.0.0.1:${runtimePort}/dashboard?welcome=1`,
      },
      body: "",
    });

    const response = await proxied({
      host: sessionHost,
      method: "GET",
      path: "/",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(302);
    expect(response.headers["location"]).toBe(
      `https://${sessionHost}/dashboard?welcome=1`,
    );

    runtime.respond = defaultRuntimeResponse;
  });

  it("gives a leaked preview URL with no cookie the neutral interstitial", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );

    const response = await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard",
      headers: {},
      body: null,
    });

    expect(response.status).toBe(404);
    expect(response.body).toContain("Nothing to open here");
    expect(controlPlane.received).toHaveLength(0);
    expect(runtime.received).toHaveLength(0);
  });

  it("refuses a session credential the control plane does not recognise", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );
    controlPlane.validSessionCredentials.clear();

    const response = await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(404);
    expect(runtime.received).toHaveLength(0);
  });

  it("fails closed on an expired route mapping", async () => {
    const host = `e1x2p3i4r5e6.${previewApex}`;
    controlPlane.routes.set(host, {
      status: 200,
      body: routeRecord({
        publicPreviewHost: host,
        upstreamOrigin: `http://127.0.0.1:${runtimePort}`,
        applicationPort: runtimePort,
        routeRevision: 4,
        readinessState: "ready",
        expiresAt: new Date(Date.now() - 1_000),
      }),
    });

    const response = await proxied({
      host,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(410);
    expect(response.body).toContain("This preview has ended");
    expect(runtime.received).toHaveLength(0);
  });

  it("fails closed when the control plane answers with a superseded route revision", async () => {
    const host = `s1t2a3l4e5r6.${previewApex}`;
    controlPlane.routes.set(host, readyRoute({ host, routeRevision: 7 }));

    const current = await proxied({
      host,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });
    expect(current.status).toBe(200);
    expect(runtime.received).toHaveLength(1);

    controlPlane.routes.set(host, readyRoute({ host, routeRevision: 6 }));
    const stale = await proxied({
      host,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(stale.status).toBe(503);
    expect(stale.body).toContain("Reconnecting to your preview");
    expect(runtime.received).toHaveLength(1);
  });

  it("serves the reconnecting page while durable readiness is withdrawn", async () => {
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

    const response = await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(503);
    expect(response.headers["retry-after"]).toBe("5");
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.body).toContain("Reconnecting to your preview");
    expect(runtime.received).toHaveLength(0);
  });

  it("refuses an upstream that is not a Vercel Sandbox port URL", async () => {
    const host = `b1i2n3d4i5n6.${previewApex}`;
    controlPlane.routes.set(host, {
      status: 200,
      body: routeRecord({
        publicPreviewHost: host,
        // An origin off the sandbox proxy domain never receives the route claim.
        upstreamOrigin: "https://attacker.example",
        applicationPort: runtimePort,
        routeRevision: 4,
        readinessState: "ready",
        expiresAt: new Date(Date.now() + 600_000),
      }),
    });

    const response = await proxied({
      host,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(502);
    expect(response.body).toContain("not reachable right now");
    expect(runtime.received).toHaveLength(0);
  });

  it("refuses a route record that states a different application port", async () => {
    const host = `p1o2r3t4m5i6.${previewApex}`;
    controlPlane.routes.set(host, {
      status: 200,
      body: routeRecord({
        publicPreviewHost: host,
        upstreamOrigin: `http://127.0.0.1:${runtimePort}`,
        applicationPort: 3000,
        routeRevision: 4,
        readinessState: "ready",
        expiresAt: new Date(Date.now() + 600_000),
      }),
    });

    const response = await proxied({
      host,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    expect(response.status).toBe(502);
    expect(runtime.received).toHaveLength(0);
  });

  it("resolves the route on every request instead of caching it", async () => {
    controlPlane.routes.set(
      sessionHost,
      readyRoute({ host: sessionHost, routeRevision: 4 }),
    );

    await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });
    await proxied({
      host: sessionHost,
      method: "GET",
      path: "/dashboard",
      headers: { cookie: sessionCookieHeader(sessionCredential) },
      body: null,
    });

    const resolutions = controlPlane.received.filter((entry) =>
      entry.url.startsWith("/v1/preview/routes/"),
    );
    expect(resolutions).toHaveLength(2);
    expect(resolutions[0]?.sessionCredential).toBe(sessionCredential);
    expect(resolutions[0]?.authorization).toBe(`Bearer ${serviceToken}`);
  });
});

const defaultRuntimeResponse: RuntimeTestServer["respond"] = (request) => ({
  status: 200,
  headers: { "content-type": "application/json; charset=utf-8" },
  body: JSON.stringify({
    product: "client preview runtime",
    path: request.url,
    preview_version_id: request.claims.preview_version_id,
  }),
});
