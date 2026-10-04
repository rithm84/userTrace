import { describe, expect, it, vi } from "vitest";

import { HttpControlPlaneClient } from "@/lib/api/client";
import { ControlPlaneError } from "@/lib/api/errors";
import { FixtureControlPlaneClient } from "@/lib/api/fixture";
import {
  FIXTURE_CASE_ID,
  FIXTURE_CLIENT_ID,
  FIXTURE_SESSION_ID,
  fixtureCases,
  fixtureClient,
} from "@/lib/api/fixture-data";

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function client(fetchImplementation: typeof fetch, readAttempts: number): HttpControlPlaneClient {
  return new HttpControlPlaneClient({
    baseUrl: "https://api.utrace.test",
    accessToken: "jwt",
    readAttempts,
    fetchImplementation,
  });
}

describe("control-plane HTTP client", () => {
  it("sends the caller's token and parses a contract-shaped response", async () => {
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureClient, 200));

    const record = await client(fetchImplementation, 1).getClient(FIXTURE_CLIENT_ID);

    expect(record.membership.role).toBe("owner");
    const [url, init] = fetchImplementation.mock.calls[0];
    expect(url).toBe(`https://api.utrace.test/v1/clients/${FIXTURE_CLIENT_ID}`);
    expect((init?.headers as Record<string, string>).authorization).toBe("Bearer jwt");
  });

  it("refuses a response that drifts from the contract instead of rendering it", async () => {
    const drifted = {
      ...fixtureClient,
      membership: { ...fixtureClient.membership, role: "superuser" },
    };
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(drifted, 200));

    await expect(client(fetchImplementation, 1).getClient(FIXTURE_CLIENT_ID)).rejects.toMatchObject({
      name: "ControlPlaneError",
      code: "invalid_response",
    });
  });

  it("maps a membership refusal to a forbidden failure", async () => {
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "no" }, 403));

    await expect(client(fetchImplementation, 1).listCases(FIXTURE_CLIENT_ID)).rejects.toMatchObject({
      code: "forbidden",
      status: 403,
    });
  });

  it("retries a read after a transport failure and raises the last error", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fetchImplementation = vi.fn<typeof fetch>().mockRejectedValue(new Error("socket closed"));

    await expect(client(fetchImplementation, 3).listCases(FIXTURE_CLIENT_ID)).rejects.toBeInstanceOf(
      ControlPlaneError,
    );

    expect(fetchImplementation).toHaveBeenCalledTimes(3);
    expect(warn).toHaveBeenCalledTimes(3);
    warn.mockRestore();
  });

  it("never repeats a write", async () => {
    const fetchImplementation = vi.fn<typeof fetch>().mockRejectedValue(new Error("socket closed"));

    await expect(
      client(fetchImplementation, 3).sendControl(FIXTURE_SESSION_ID, "pause"),
    ).rejects.toMatchObject({ code: "transport" });

    expect(fetchImplementation).toHaveBeenCalledTimes(1);
  });
});

describe("fixture control plane", () => {
  it("answers the Shopalytics case and refuses another client", async () => {
    const fixture = new FixtureControlPlaneClient();

    const detail = await fixture.getCase(FIXTURE_CLIENT_ID, FIXTURE_CASE_ID);
    expect(detail.validation_attempts[0].outcome).toBe("validated");
    expect(detail.offer?.amount_minor_units).toBe(25_000);

    await expect(fixture.listCases("someone-else")).rejects.toMatchObject({ code: "forbidden" });
  });

  it("refuses to stop a case that already reached a terminal state", async () => {
    const fixture = new FixtureControlPlaneClient();

    await expect(
      fixture.stopCase(FIXTURE_CLIENT_ID, FIXTURE_CASE_ID, "No longer relevant"),
    ).rejects.toMatchObject({ code: "conflict" });
  });

  it("reports what a stop withdrew on a case that still has authority", async () => {
    const fixture = new FixtureControlPlaneClient();
    const nonterminal = fixtureCases.cases.find((item) => item.state === "gate_pending");
    if (nonterminal === undefined) {
      throw new Error("the fixture carries no nonterminal case to stop");
    }

    const stopped = await fixture.stopCase(FIXTURE_CLIENT_ID, nonterminal.id, "Handled by support");

    expect(stopped.product_signal_id).toBe(nonterminal.id);
    expect(stopped.participation_occurred).toBe(true);
    expect(stopped.revoked_outreach_attempts).toBeGreaterThan(0);
  });

  it("will not lower the allowance below what outstanding promises consume", async () => {
    const fixture = new FixtureControlPlaneClient();

    await expect(
      fixture.authorizeOffersBudget(FIXTURE_CLIENT_ID, {
        currency: "USD",
        authorized_minor_units: 1000,
      }),
    ).rejects.toMatchObject({ code: "rejected" });
  });

  it("resolves an opaque reference it does not know without inventing a label", async () => {
    const fixture = new FixtureControlPlaneClient();

    const [resolved] = await fixture.resolveReferences(FIXTURE_CLIENT_ID, [
      "customer_account:0199a0c0-0000-7aaa-8000-0000000000ff",
    ]);

    expect(resolved.label).toBeUndefined();
  });
});
