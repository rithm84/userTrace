/**
 * Real-Chrome proof of the failure paths.
 *
 * An impossible acknowledgement, an unverifiable attestation, and a reload each
 * have to behave as the contract says: an integrity failure rather than a
 * reconnect hint, a refusal to boot unobserved, and a new browser stream.
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";
import { settle, startHarness, waitFor, waitForReady, type Harness } from "./support/harness.js";

let harness: Harness;

/** Polls an asynchronous page read until the predicate holds. */
const pollUntil = async <T>(
  read: () => Promise<T>,
  predicate: (value: T) => boolean,
  timeoutMs: number,
  description: string,
): Promise<T> => {
  const deadline = Date.now() + timeoutMs;
  let value = await read();
  while (!predicate(value)) {
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for ${description}; last value ${JSON.stringify(value)}`);
    }
    await new Promise<void>((done) => setTimeout(done, 100));
    value = await read();
  }
  return value;
};

beforeAll(async () => {
  harness = await startHarness({ maxObservationBytes: MAX_OBSERVATION_BYTES, path: "/dashboard/conversions" });
  await waitForReady(harness.page);
}, 120_000);

afterAll(async () => {
  await harness.stop();
});

describe("stream identity", () => {
  it("starts a new browser stream at export sequence one after a reload", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    const firstStream = harness.ingest.streamIds[0];
    await harness.reload();
    await waitForReady(harness.page);
    await waitFor(
      () => harness.ingest.streamIds.length,
      (count) => count >= 2,
      15_000,
      "a second browser stream after the reload",
    );
    const secondStream = harness.ingest.streamIds[1];
    expect(secondStream).not.toBe(firstStream);
    expect(harness.ingest.resumeRequests[1]).toBe(0);
    const health = await harness.page.evaluate(() => window.__utraceTest?.health());
    expect(health?.state).toBe("ready");
    expect(health?.browser_stream_id).toBe(secondStream);
    expect(health?.latest_export_sequence).toBeGreaterThanOrEqual(1);
  });
});

describe("acknowledgement integrity", () => {
  it("treats an acknowledgement beyond the produced sequence as a failure", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    harness.ingest.sendImpossibleAck(harness.ingest.acknowledged() + 5000);
    const after = await pollUntil(
      () => harness.page.evaluate(() => window.__utraceTest?.health()),
      (health) => health?.state === "failed",
      10_000,
      "the runtime to report an integrity failure",
    );
    expect(after?.failure_code).toBe("acknowledgement_integrity_failure");
    expect(after?.failure_detail).toContain("beyond produced");
    expect(Object.values(after?.components ?? {}).some(Boolean)).toBe(false);
  });

  it("stops exporting once readiness is withdrawn", async () => {
    const before = harness.ingest.observations.length;
    await harness.page.click("#mutate");
    await new Promise<void>((done) => setTimeout(done, 600));
    expect(harness.ingest.observations.length).toBe(before);
  });
});

describe("attestation verification", () => {
  it("refuses to boot when the attestation does not verify against the packaged key", async () => {
    harness.tamperNextAttestation.value = true;
    await harness.reload();
    await harness.page.waitForFunction(() => window.__utraceTest !== undefined, undefined, { timeout: 15_000 });
    const message = await pollUntil(
      () => harness.page.evaluate(() => window.__utraceTest?.activationError()),
      (value) => typeof value === "string" && value.length > 0,
      15_000,
      "the activation failure the fixture recorded",
    );
    expect(message).toContain("attestation_invalid_or_expired");
    expect(message).toContain("signature does not verify");
    const health = await harness.page.evaluate(() => {
      try {
        return window.__utraceTest?.health().state ?? "unknown";
      } catch {
        return "no_session";
      }
    });
    expect(health).toBe("no_session");
  });
});
