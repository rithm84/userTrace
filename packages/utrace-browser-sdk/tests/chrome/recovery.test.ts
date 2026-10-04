/**
 * Real-Chrome proof of telemetry stream recovery.
 *
 * A network cut while the telemetry credential is unexpired reconnects the same
 * browser stream with that credential and runs no attestation. Once the
 * credential has expired, recovery needs a fresh attestation, and the new
 * activation starts a new stream rather than resuming the old one.
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";
import type { RuntimeHealth } from "../../src/runtime/readiness.js";
import { CREDENTIAL_LIFETIME_MS, settle, startHarness, waitFor, waitForReady, type Harness } from "./support/harness.js";

let harness: Harness;

const health = async (): Promise<RuntimeHealth> => {
  const current = await harness.page.evaluate(() => window.__utraceTest?.health());
  if (current === undefined) throw new Error("the fixture exposes no runtime health");
  return current;
};

beforeAll(async () => {
  harness = await startHarness({ maxObservationBytes: MAX_OBSERVATION_BYTES, path: "/dashboard/conversions" });
  await waitForReady(harness.page);
}, 120_000);

afterAll(async () => {
  await harness.stop();
});

describe("telemetry stream recovery", () => {
  it("reconnects a dropped stream with its unexpired credential and no new attestation", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    const before = await health();
    const activations = harness.controlPlane.activationCount();
    const opens = harness.ingest.streamIds.length;
    const credential = harness.ingest.credentialsSeen.at(-1);

    harness.ingest.dropConnection();
    await waitFor(() => harness.ingest.streamIds.length, (count) => count > opens, 15_000, "the stream to reopen");
    const after = await waitFor(health, (current) => current.state === "ready", 15_000, "the runtime to recover");

    expect(harness.controlPlane.activationCount()).toBe(activations);
    expect(harness.ingest.credentialsSeen.at(-1)).toBe(credential);
    expect(harness.ingest.streamIds.at(-1)).toBe(before.browser_stream_id);
    expect(harness.ingest.resumeRequests.at(-1)).toBe(before.acknowledged_export_sequence);
    expect(after.browser_stream_id).toBe(before.browser_stream_id);
    expect(after.document_attachment_id).toBe(before.document_attachment_id);

    await harness.page.click("#save-view");
    const delivered = await waitFor(
      () =>
        harness.ingest.observations.filter(
          (o) => o.observation_type === "interaction.activation" && o.export_sequence > before.latest_export_sequence,
        ),
      (found) => found.length > 0,
      10_000,
      "an interaction exported on the reconnected stream",
    );
    expect(delivered[0]?.document_attachment_id).toBe(before.document_attachment_id);
  });

  it("starts a new stream under a fresh attestation once the credential has expired", async () => {
    harness.credentialLifetimeMs.value = 2_000;
    await harness.reload();
    await waitForReady(harness.page);
    harness.credentialLifetimeMs.value = CREDENTIAL_LIFETIME_MS;
    const before = await health();
    const activations = harness.controlPlane.activationCount();

    // The credential was issued before the runtime became ready, so it has
    // lapsed by now; the live stream keeps exporting until the cut.
    await new Promise<void>((done) => setTimeout(done, 2_000));
    harness.ingest.dropConnection();
    const after = await waitFor(
      health,
      (current) => current.state === "ready" && current.browser_stream_id !== before.browser_stream_id,
      15_000,
      "a ready runtime on a new stream",
    );

    expect(harness.controlPlane.activationCount()).toBe(activations + 1);
    expect(after.runtime_id).toBe(before.runtime_id);
    expect(after.document_attachment_id).not.toBe(before.document_attachment_id);
    expect(harness.ingest.streamIds.at(-1)).toBe(after.browser_stream_id);
    expect(harness.ingest.resumeRequests.at(-1)).toBe(0);
    const newStream = harness.ingest.observations.filter(
      (o) => o.document_attachment_id === after.document_attachment_id,
    );
    expect(Math.min(...newStream.map((o) => o.export_sequence))).toBe(1);
    expect(
      newStream.some((o) => o.observation_type === "baseline.snapshot" && o.payload["reason"] === "activation"),
    ).toBe(true);
  });
});
