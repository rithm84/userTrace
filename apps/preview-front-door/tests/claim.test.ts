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
  sessionCookieName,
  sessionCredential,
  serviceToken,
} from "./helpers/door-fixture";

/**
 * The authenticated claim: `/__utrace/claim?code=` exchanges a one-time handoff for the
 * origin-bound session cookie. A bare or leaked URL gets the neutral interstitial.
 */

const doorPort = 8601;
const controlPlanePort = 8611;
const sessionHost = `c1a2m3k9x7q4.${previewApex}`;
const handoffCode = "hx7Qm2Zt9Lb4Nv6Ks1Jd3Pf0";

let controlPlane: ControlPlaneTestServer;
let door: DoorTestServer;

const exchangeAnswer = (params: {
  host: string;
  expiresAt: Date;
}): { status: number; body: unknown } => ({
  status: 200,
  body: {
    schema_version: "utrace_preview_handoff_exchange_v1",
    public_preview_host: params.host,
    client_id: identities.clientId,
    interview_session_id: identities.interviewSessionId,
    session_credential: {
      value: sessionCredential,
      expires_at: params.expiresAt.toISOString(),
    },
  },
});

const claim = (params: { code: string; host: string }) =>
  doorRequest({
    port: doorPort,
    hostHeader: params.host,
    method: "GET",
    path: `/__utrace/claim?code=${encodeURIComponent(params.code)}`,
    headers: {},
    body: null,
  });

const cookieAttributes = (setCookie: string): readonly string[] =>
  setCookie
    .split(";")
    .slice(1)
    .map((attribute) => attribute.trim());

beforeAll(async () => {
  controlPlane = await startControlPlaneServer({
    port: controlPlanePort,
    serviceToken,
  });
  configureDoor({
    controlPlaneBaseUrl: controlPlane.baseUrl,
    applicationPort: 3000,
    routeContextTtlSeconds: 30,
    controlPlaneAttempts: 2,
  });
  door = await startDoorServer({ port: doorPort });
});

afterAll(async () => {
  await door.close();
  await controlPlane.close();
});

beforeEach(() => {
  controlPlane.handoffs.clear();
  controlPlane.received.length = 0;
});

describe("claim", () => {
  it("exchanges a one-time code for a __Host- cookie bound to the session origin", async () => {
    controlPlane.handoffs.set(
      handoffCode,
      exchangeAnswer({
        host: sessionHost,
        expiresAt: new Date(Date.now() + 3_600_000),
      }),
    );

    const response = await claim({ code: handoffCode, host: sessionHost });

    expect(response.status).toBe(303);
    // The claim admits the browser to the origin; the product sign-in comes next.
    expect(response.headers["location"]).toBe("/__utrace/sign-in");
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.headers["vary"]).toBe("Cookie");
    expect(response.setCookies).toHaveLength(1);

    const cookie = response.setCookies[0] as string;
    expect(cookie.startsWith(`${sessionCookieName}=${sessionCredential};`)).toBe(
      true,
    );
    const attributes = cookieAttributes(cookie);
    expect(attributes).toContain("Path=/");
    expect(attributes).toContain("Secure");
    expect(attributes).toContain("HttpOnly");
    expect(attributes).toContain("SameSite=Lax");
    const maxAge = Number(/Max-Age=(\d+)/.exec(cookie)?.[1]);
    expect(maxAge).toBeGreaterThan(3590);
    expect(maxAge).toBeLessThanOrEqual(3600);
    // `__Host-` forbids a Domain attribute, which is what pins the cookie to this origin.
    expect(cookie.toLowerCase()).not.toContain("domain=");
  });

  it("sends the contract exchange body and keeps the code out of the cookie lane", async () => {
    controlPlane.handoffs.set(
      handoffCode,
      exchangeAnswer({
        host: sessionHost,
        expiresAt: new Date(Date.now() + 3_600_000),
      }),
    );

    await claim({ code: handoffCode, host: sessionHost });

    expect(controlPlane.received).toHaveLength(1);
    const received = controlPlane.received[0];
    expect(received?.method).toBe("POST");
    expect(received?.url).toBe(
      `/v1/preview/handoffs/${handoffCode}/exchange`,
    );
    expect(received?.authorization).toBe(`Bearer ${serviceToken}`);
    expect(received?.sessionCredential).toBeNull();
    expect(received?.body).toStrictEqual({
      schema_version: "utrace_preview_handoff_exchange_request_v1",
      public_preview_host: sessionHost,
    });
  });

  it("clamps the cookie lifetime to the session credential's own expiry", async () => {
    controlPlane.handoffs.set(
      handoffCode,
      exchangeAnswer({
        host: sessionHost,
        expiresAt: new Date(Date.now() + 120_000),
      }),
    );

    const response = await claim({ code: handoffCode, host: sessionHost });
    const maxAge = Number(
      /Max-Age=(\d+)/.exec(response.setCookies[0] as string)?.[1],
    );

    expect(maxAge).toBeGreaterThan(100);
    expect(maxAge).toBeLessThanOrEqual(120);
  });

  it("gives a bare claim URL with no code the neutral interstitial", async () => {
    const response = await doorRequest({
      port: doorPort,
      hostHeader: sessionHost,
      method: "GET",
      path: "/__utrace/claim",
      headers: {},
      body: null,
    });

    expect(response.status).toBe(404);
    expect(response.setCookies).toHaveLength(0);
    expect(response.body).toContain("Nothing to open here");
    expect(controlPlane.received).toHaveLength(0);
  });

  it("gives an unknown or consumed code the neutral interstitial and no cookie", async () => {
    const response = await claim({
      code: "zzzz9999aaaa8888bbbb7777",
      host: sessionHost,
    });

    expect(response.status).toBe(404);
    expect(response.setCookies).toHaveLength(0);
    expect(response.body).toContain("Nothing to open here");
  });

  it("shows the ended page when the session behind the code is closed", async () => {
    controlPlane.handoffs.set(handoffCode, {
      status: 410,
      body: {
        schema_version: "utrace_preview_denial_v1",
        reason: "session_ended",
      },
    });

    const response = await claim({ code: handoffCode, host: sessionHost });

    expect(response.status).toBe(410);
    expect(response.setCookies).toHaveLength(0);
    expect(response.body).toContain("This preview has ended");
  });

  it("refuses an exchange answered for a different session origin", async () => {
    controlPlane.handoffs.set(
      handoffCode,
      exchangeAnswer({
        host: `other9host4here.${previewApex}`,
        expiresAt: new Date(Date.now() + 3_600_000),
      }),
    );

    const response = await claim({ code: handoffCode, host: sessionHost });

    expect(response.status).toBe(404);
    expect(response.setCookies).toHaveLength(0);
  });

  it("refuses a claim on the preview apex itself", async () => {
    const response = await claim({ code: handoffCode, host: previewApex });

    expect(response.status).toBe(404);
    expect(response.setCookies).toHaveLength(0);
    expect(controlPlane.received).toHaveLength(0);
  });
});
