/**
 * Real-Chrome proof of the instrumented child frame path.
 *
 * The child authenticates its own document, joins the top-document coordinator
 * over an authenticated MessageChannel, and never exports: the coordinator
 * re-envelopes its records into the one browser stream for the page.
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";
import { settle, startHarness, waitFor, waitForReady, type Harness } from "./support/harness.js";

let harness: Harness;
let frameIdentity: { runtimeId: string; frameId: string; attached: boolean };

beforeAll(async () => {
  harness = await startHarness({ maxObservationBytes: MAX_OBSERVATION_BYTES, path: "/dashboard/conversions/framed" });
  await waitForReady(harness.page);
  const frame = harness.page.frames().find((candidate) => candidate.url().endsWith("/frame"));
  if (frame === undefined) throw new Error("the embedded report frame never loaded");
  await frame.waitForFunction(() => window.__utraceFrameTest !== undefined, undefined, { timeout: 15_000 });
  await frame.evaluate(async () => {
    await window.__utraceFrameTest?.ready;
  });
  frameIdentity = await frame.evaluate(() => {
    const surface = window.__utraceFrameTest;
    if (surface === undefined) throw new Error("the frame fixture is not ready");
    return surface.attachment();
  });
}, 120_000);

afterAll(async () => {
  await harness.stop();
});

describe("instrumented child frame", () => {
  it("attaches to the coordinator with its own runtime and document identity", () => {
    expect(frameIdentity.attached).toBe(true);
    expect(frameIdentity.frameId).toMatch(/^[0-9a-f-]{36}$/);
    expect(frameIdentity.runtimeId).not.toBe(harness.ingest.observations[0]?.runtime_id);
  });

  it("activates its own document without taking a telemetry credential", () => {
    // Two activations, one per document; one credential, for the exporter only.
    expect(harness.controlPlane.activationCount()).toBe(2);
    expect(harness.ingest.credentialsSeen).toHaveLength(1);
    expect(harness.ingest.streamIds).toHaveLength(1);
  });

  it("forwards its baseline through the coordinator into the one browser stream", async () => {
    const forwarded = await waitFor(
      () => harness.ingest.observations.filter((o) => o.frame_id === frameIdentity.frameId),
      (found) => found.some((record) => record.observation_type === "baseline.snapshot"),
      15_000,
      "the child frame baseline",
    );
    const baseline = forwarded.find((record) => record.observation_type === "baseline.snapshot");
    expect(baseline?.runtime_id).toBe(frameIdentity.runtimeId);
    expect(baseline?.export_sequence).toBeGreaterThan(0);
    const payload = baseline?.payload as { surfaces: string[]; landmarks: { role: string }[] };
    expect(payload.surfaces).toContain("conversion_dashboard");
    expect(payload.landmarks.some((landmark) => landmark.role === "main")).toBe(true);
  });

  it("forwards an interaction inside the frame with the child's own source sequence", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    const frame = harness.page.frames().find((candidate) => candidate.url().endsWith("/frame"));
    if (frame === undefined) throw new Error("the embedded report frame disappeared");
    await frame.click("#frame-action");
    const activations = await waitFor(
      () =>
        harness.ingest.observations.filter(
          (o) => o.frame_id === frameIdentity.frameId && o.observation_type === "interaction.activation",
        ),
      (found) => found.length >= 1,
      15_000,
      "an activation forwarded from the child frame",
    );
    expect(activations[0]?.payload["role"]).toBe("button");
    expect(activations[0]?.source_sequence).toBeGreaterThan(0);
    // Every record on the stream keeps one contiguous export sequence, whichever
    // document produced it.
    const sequences = harness.ingest.observations.map((o) => o.export_sequence).sort((a, b) => a - b);
    for (let index = 1; index < sequences.length; index += 1) {
      expect(sequences[index]).toBe((sequences[index - 1] ?? 0) + 1);
    }
  });

  it("keeps the top document the only exporter", () => {
    const frameIds = new Set(harness.ingest.observations.map((o) => o.frame_id));
    expect([...frameIds].sort()).toEqual(["top", frameIdentity.frameId].sort());
    expect(harness.ingest.streamIds).toHaveLength(1);
  });
});
