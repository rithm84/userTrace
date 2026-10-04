import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import {
  startControlPlaneServer,
  type ControlPlaneTestServer,
} from "./helpers/control-plane-server";
import { startDoorServer, type DoorTestServer } from "./helpers/door-server";
import { doorRequest } from "./helpers/http-client";
import {
  activationRequest,
  activationResponse,
  configureDoor,
  documentAttestation,
  documentNonce,
  previewApex,
  serviceToken,
  sessionCookieHeader,
  sessionCredential,
  telemetryCredential,
} from "./helpers/door-fixture";

/**
 * The SDK activation relay: the reserved same-origin endpoints the embedded browser SDK
 * bootstrap calls before the preview hydrates.
 */

const doorPort = 8603;
const controlPlanePort = 8613;
const sessionHost = `a1c2t3i4v5a6.${previewApex}`;

/** The activation body the SDK posts, bound to this suite's session origin. */
const activationBody = JSON.stringify({
  ...activationRequest,
  document_nonce: documentNonce,
  document_origin: `https://${sessionHost}`,
});
const credentialBody = JSON.stringify({
  attestation: documentAttestation,
  document_nonce: documentNonce,
});

let controlPlane: ControlPlaneTestServer;
let door: DoorTestServer;

const relay = (params: {
  path: string;
  headers: Readonly<Record<string, string>>;
  body: string | null;
}) =>
  doorRequest({
    port: doorPort,
    hostHeader: sessionHost,
    method: "POST",
    path: params.path,
    headers: { "content-type": "application/json", ...params.headers },
    body: params.body,
  });

const sameOrigin = {
  origin: `https://${sessionHost}`,
  cookie: sessionCookieHeader(sessionCredential),
} as const;

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
  controlPlane.received.length = 0;
  controlPlane.validSessionCredentials.clear();
  controlPlane.validSessionCredentials.add(sessionCredential);
  controlPlane.activation = { status: 200, body: activationResponse };
  controlPlane.telemetryCredential = { status: 200, body: telemetryCredential };
});

describe("activation relay", () => {
  it("relays a document activation and returns all three signed envelopes unchanged", async () => {
    const response = await relay({
      path: "/__utrace/activate",
      headers: sameOrigin,
      body: activationBody,
    });

    expect(response.status).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(JSON.parse(response.body)).toStrictEqual(activationResponse);

    const relayed = controlPlane.received[0];
    expect(relayed?.url).toBe("/v1/preview/activations");
    expect(relayed?.sessionCredential).toBe(sessionCredential);
    expect(relayed?.body).toStrictEqual({
      schema_version: "utrace_preview_activation_relay_request_v1",
      public_preview_host: sessionHost,
      activation_request: JSON.parse(activationBody),
    });
  });

  it("refuses an activation without the session cookie", async () => {
    const response = await relay({
      path: "/__utrace/activate",
      headers: { origin: sameOrigin.origin },
      body: activationBody,
    });

    expect(response.status).toBe(403);
    expect(JSON.parse(response.body)).toStrictEqual({
      schema_version: "utrace_preview_relay_failure_v1",
      reason: "unauthorized",
    });
    expect(controlPlane.received).toHaveLength(0);
  });

  it("refuses a cross-origin activation", async () => {
    const response = await relay({
      path: "/__utrace/activate",
      headers: { ...sameOrigin, origin: "https://attacker.example" },
      body: activationBody,
    });

    expect(response.status).toBe(400);
    expect(JSON.parse(response.body)).toStrictEqual({
      schema_version: "utrace_preview_relay_failure_v1",
      reason: "invalid_request",
    });
    expect(controlPlane.received).toHaveLength(0);
  });

  it("refuses a body that does not match the activation contract", async () => {
    const response = await relay({
      path: "/__utrace/activate",
      headers: sameOrigin,
      body: JSON.stringify({ ...JSON.parse(activationBody), document_nonce: "nonce with spaces" }),
    });

    expect(response.status).toBe(400);
    expect(controlPlane.received).toHaveLength(0);
  });

  it("does not disclose the control plane's typed denial to the browser", async () => {
    controlPlane.activation = {
      status: 410,
      body: {
        schema_version: "utrace_preview_denial_v1",
        reason: "session_ended",
      },
    };

    const response = await relay({
      path: "/__utrace/activate",
      headers: sameOrigin,
      body: activationBody,
    });

    expect(response.status).toBe(403);
    expect(response.body).not.toContain("session_ended");
  });
});

describe("telemetry credential relay", () => {
  it("exchanges a verified attestation for a telemetry credential and its limits", async () => {
    const response = await relay({
      path: "/__utrace/telemetry-credential",
      headers: sameOrigin,
      body: credentialBody,
    });

    expect(response.status).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(JSON.parse(response.body)).toStrictEqual(telemetryCredential);

    const relayed = controlPlane.received[0];
    expect(relayed?.url).toBe("/v1/preview/telemetry-credentials");
    expect(relayed?.sessionCredential).toBe(sessionCredential);
    expect(relayed?.body).toStrictEqual({
      schema_version: "utrace_preview_telemetry_credential_relay_request_v1",
      public_preview_host: sessionHost,
      credential_request: JSON.parse(credentialBody),
    });
  });

  it("refuses an attestation that does not have the shape of a signed envelope", async () => {
    const response = await relay({
      path: "/__utrace/telemetry-credential",
      headers: sameOrigin,
      body: JSON.stringify({
        attestation: { ...documentAttestation, signature_b64url: "short" },
        document_nonce: documentNonce,
      }),
    });

    expect(response.status).toBe(400);
    expect(controlPlane.received).toHaveLength(0);
  });

  it("refuses a relay on an unknown host", async () => {
    const response = await doorRequest({
      port: doorPort,
      hostHeader: previewApex,
      method: "POST",
      path: "/__utrace/telemetry-credential",
      headers: {
        "content-type": "application/json",
        origin: `https://${previewApex}`,
        cookie: sessionCookieHeader(sessionCredential),
      },
      body: credentialBody,
    });

    expect(response.status).toBe(403);
    expect(controlPlane.received).toHaveLength(0);
  });
});

describe("the trace propagation probe", () => {
  it("echoes exactly the trace context the browser's own injection path produced", async () => {
    const traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
    const response = await relay({
      path: "/__utrace/propagation-probe",
      headers: { ...sameOrigin, traceparent, tracestate: "utrace=1" },
      body: JSON.stringify({ probe: "trace_propagation" }),
    });

    expect(response.status).toBe(200);
    expect(JSON.parse(response.body)).toStrictEqual({
      traceparent,
      tracestate: "utrace=1",
      traceparent_count: 1,
    });
    // The probe proves the browser's injection path, so it reaches no control
    // plane and no preview runtime.
    expect(controlPlane.received).toHaveLength(0);
  });

  it("reports a second injector rather than tolerating the duplicate", async () => {
    const response = await relay({
      path: "/__utrace/propagation-probe",
      headers: {
        ...sameOrigin,
        traceparent:
          "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01,00-1bf92f3577b34da6a3ce929d0e0e4736-10f067aa0ba902b7-01",
      },
      body: JSON.stringify({ probe: "trace_propagation" }),
    });

    expect(JSON.parse(response.body).traceparent_count).toBe(2);
  });

  it("refuses a probe without the session cookie", async () => {
    const response = await relay({
      path: "/__utrace/propagation-probe",
      headers: { origin: sameOrigin.origin, traceparent: "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" },
      body: JSON.stringify({ probe: "trace_propagation" }),
    });

    expect(response.status).toBe(403);
  });
});
