import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import {
  startControlPlaneServer,
  type ControlPlaneTestServer,
} from "./helpers/control-plane-server";
import { startDoorServer, type DoorTestServer } from "./helpers/door-server";
import { doorRequest } from "./helpers/http-client";
import {
  configureDoor,
  identities,
  previewApex,
  previewEntryToken,
  routeContextSigningSecret,
  routeRecord,
  serviceToken,
  sessionCookieHeader,
  sessionCookieName,
  sessionCredential,
} from "./helpers/door-fixture";
import { startRuntimeServer, type RuntimeTestServer } from "./helpers/runtime-server";

/**
 * The product sign-in the claim hands the browser on to: the door posts a one-time token
 * to the runtime's declared sign-in path and gives the browser the product's own cookie.
 */

const doorPort = 8605;
const controlPlanePort = 8615;
const runtimePort = 8625;
const sessionHost = `s1g2n3i4n5x6.${previewApex}`;
const signInPath = "/api/utrace/preview-handoff";
const productToken = "v1.eyJ0b2tlbl9pZCI6InRlc3QifQ.c2lnbmF0dXJlLWJ5dGVz";
const productCookie = "shopalytics_preview_session=signed; Path=/; HttpOnly; Secure; SameSite=Lax";

let controlPlane: ControlPlaneTestServer;
let runtime: RuntimeTestServer;
let door: DoorTestServer;

const runtimeCookie = `${sessionCookieName}-runtime=${identities.previewRuntimeAllocationId}; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=Lax`;

const signIn = (cookie: string | null, path: string) =>
  doorRequest({
    port: doorPort,
    hostHeader: sessionHost,
    method: "GET",
    path,
    headers: cookie === null ? {} : { cookie },
    body: null,
  });

beforeAll(async () => {
  controlPlane = await startControlPlaneServer({ port: controlPlanePort, serviceToken });
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
  controlPlane.received.length = 0;
  controlPlane.validSessionCredentials.clear();
  controlPlane.validSessionCredentials.add(sessionCredential);
  controlPlane.routes.set(sessionHost, {
    status: 200,
    body: routeRecord({
      publicPreviewHost: sessionHost,
      upstreamOrigin: runtime.origin,
      applicationPort: runtimePort,
      routeRevision: 1,
      readinessState: "ready",
      expiresAt: new Date(Date.now() + 600_000),
    }),
  });
  controlPlane.productSignIn = {
    status: 200,
    body: {
      schema_version: "utrace_preview_product_sign_in_v1",
      path: signInPath,
      token: productToken,
    },
  };
  runtime.received.length = 0;
  runtime.respond = () => ({
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "set-cookie": [productCookie],
    },
    body: JSON.stringify({ state: "signed_in", redirect_to: "/app/conversion" }),
  });
});

describe("product sign-in", () => {
  it("posts the one-time token server-side and hands the browser the product's cookie", async () => {
    const response = await signIn(sessionCookieHeader(sessionCredential), "/__utrace/sign-in");

    expect(response.status).toBe(303);
    expect(response.headers["location"]).toBe("/app/conversion");
    expect(response.headers["cache-control"]).toBe("no-store");
    // The door also records which runtime the product signed the browser into.
    expect(response.setCookies).toStrictEqual([productCookie, runtimeCookie]);
    expect(response.body).not.toContain(productToken);

    expect(runtime.received).toHaveLength(1);
    const posted = runtime.received[0];
    expect(posted?.method).toBe("POST");
    expect(posted?.url).toBe(signInPath);
    expect(JSON.parse(posted?.body ?? "null")).toStrictEqual({ token: productToken });
    expect(posted?.headers["x-utrace-preview-entry"]).toBe(previewEntryToken);
    expect(posted?.claims.public_preview_host).toBe(sessionHost);
    // The door's own session credential never reaches the product.
    expect(posted?.headers["cookie"]).toBeUndefined();
  });

  it("returns to the page that asked for a fresh sign-in, and only to a page on this origin", async () => {
    const returning = await signIn(
      sessionCookieHeader(sessionCredential),
      `/__utrace/sign-in?return_to=${encodeURIComponent("/app/conversion?range=last_30_days")}`,
    );
    expect(returning.status).toBe(303);
    expect(returning.headers["location"]).toBe("/app/conversion?range=last_30_days");

    const elsewhere = await signIn(
      sessionCookieHeader(sessionCredential),
      `/__utrace/sign-in?return_to=${encodeURIComponent("//attacker.example/app")}`,
    );
    expect(elsewhere.headers["location"]).toBe("/app/conversion");
  });

  it("fails rather than leaving the participant at a login form when the product refuses", async () => {
    runtime.respond = () => ({
      status: 401,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify({ state: "refused", reason: "replayed" }),
    });

    const response = await signIn(sessionCookieHeader(sessionCredential), "/__utrace/sign-in");

    expect(response.status).toBe(502);
    expect(response.setCookies).toHaveLength(0);
  });

  it("gives a browser that never claimed the neutral interstitial and asks for no token", async () => {
    const response = await signIn(null, "/__utrace/sign-in");

    expect(response.status).toBe(404);
    expect(controlPlane.received).toHaveLength(0);
    expect(runtime.received).toHaveLength(0);
  });

  it("shows the ended page when the control plane refuses to sign in to a closed session", async () => {
    controlPlane.productSignIn = {
      status: 410,
      body: { schema_version: "utrace_preview_denial_v1", reason: "session_ended" },
    };

    const response = await signIn(sessionCookieHeader(sessionCredential), "/__utrace/sign-in");

    expect(response.status).toBe(410);
    expect(response.body).toContain("This preview has ended");
    expect(runtime.received).toHaveLength(0);
  });
});
