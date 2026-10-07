/**
 * Real-Chrome proof of the shared observation runtime.
 *
 * Every assertion runs against Google Chrome driving the static fixture, the
 * test preview front door, and the test ingestion WebSocket. Nothing in this
 * file mocks the SDK; the counterpart servers implement the documented
 * contracts of the preview front door, control plane, and telemetry ingestion,
 * which other workstreams own.
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";
import { settle, startHarness, waitFor, waitForReady, type Harness } from "./support/harness.js";

let harness: Harness;

const sleep = (ms: number): Promise<void> => new Promise<void>((done) => setTimeout(done, ms));

beforeAll(async () => {
  harness = await startHarness({ maxObservationBytes: MAX_OBSERVATION_BYTES, path: "/dashboard/conversions" });
  await waitForReady(harness.page);
}, 120_000);

afterAll(async () => {
  await harness.stop();
});

describe("activation and readiness", () => {
  it("reaches ready with every readiness component and each hook installed once", async () => {
    const health = await harness.page.evaluate(() => window.__utraceTest?.health());
    expect(health?.state).toBe("ready");
    expect(health?.hooks).toEqual({
      fetch: true,
      xhr: true,
      history: true,
      errors: true,
      interactions: true,
      mutations: true,
    });
    expect(Object.values(health?.components ?? {}).every(Boolean)).toBe(true);
    expect(health?.failure_code).toBeNull();
    expect(health?.annotation_manifest_version).toBe("test-manifest-1");
  });

  it("refuses a second runtime in the same document", async () => {
    const message = await harness.page.evaluate(async () => window.__utraceTest?.activateAgain());
    expect(message).toContain("duplicate_runtime_or_hook");
    expect(message).toContain("already hosts a uTrace runtime");
  });

  it("exchanged one attestation for one write-only credential", () => {
    expect(harness.controlPlane.activationCount()).toBe(1);
    expect(harness.ingest.credentialsSeen).toHaveLength(1);
    const credential = harness.ingest.credentialsSeen[0] ?? "";
    expect(credential.length).toBeGreaterThan(16);
  });

  it("keeps the attestation and credential out of the URL and browser storage", async () => {
    const leaked = await harness.page.evaluate(() => ({
      url: window.location.href,
      local: Object.keys(window.localStorage),
      session: Object.keys(window.sessionStorage),
      cookie: document.cookie,
    }));
    expect(leaked.url).not.toContain("attestation");
    expect(leaked.url).not.toContain("token");
    expect(leaked.local).toEqual([]);
    expect(leaked.session).toEqual([]);
    expect(leaked.cookie).toBe("");
  });
});

describe("baseline, sequences, and acknowledgement", () => {
  it("emits an accepted baseline as the first record of a new stream", () => {
    const first = harness.ingest.observations[0];
    expect(first?.observation_type).toBe("baseline.snapshot");
    expect(first?.export_sequence).toBe(1);
    expect(first?.source_sequence).toBeGreaterThanOrEqual(1);
    expect(harness.ingest.streamIds).toHaveLength(1);
    expect(harness.ingest.resumeRequests).toEqual([0]);
  });

  it("projects the annotated surfaces, entities, and registered targets", () => {
    const baseline = harness.ingest.observations.find((o) => o.observation_type === "baseline.snapshot");
    const payload = baseline?.payload as {
      surfaces: string[];
      entities: { entity_definition_id: string; instance_ref: string }[];
      targets: { definition_id: string | null; visual_only: boolean }[];
      landmarks: { role: string }[];
    };
    expect(payload.surfaces).toContain("conversion_dashboard");
    expect(payload.surfaces).toContain("billing_settings");
    expect(payload.entities[0]?.entity_definition_id).toBe("conversion_view");
    // The entity instance ref is pseudonymized by its signed rule.
    expect(payload.entities[0]?.instance_ref).toMatch(/^psn_\d+$/);
    expect(payload.targets.some((target) => target.definition_id === "saved_view_save_button")).toBe(true);
    expect(payload.targets.some((target) => target.visual_only)).toBe(true);
    // A declared visual target keeps its declared definition.
    expect(
      payload.targets.some((target) => target.definition_id === "saved_view_list" && target.visual_only),
    ).toBe(true);
    expect(payload.landmarks.length).toBeGreaterThan(0);
  });

  it("assigns contiguous export sequences and advances the acknowledgement", async () => {
    await harness.page.click("#mutate");
    await waitFor(
      () => harness.ingest.observations.length,
      (count) => count > 3,
      10_000,
      "more observations after a product interaction",
    );
    const sequences = harness.ingest.observations.map((o) => o.export_sequence).sort((a, b) => a - b);
    expect(sequences[0]).toBe(1);
    for (let index = 1; index < sequences.length; index += 1) {
      expect(sequences[index]).toBe((sequences[index - 1] ?? 0) + 1);
    }
    await waitFor(
      () => harness.ingest.acknowledged(),
      (acknowledged) => acknowledged >= (sequences.at(-1) ?? 0),
      10_000,
      "the acknowledgement watermark to reach the last produced sequence",
    );
  });

  it("carries every envelope field on each record", () => {
    for (const observation of harness.ingest.observations) {
      expect(observation.observation_id).toMatch(/^[0-9a-f-]{36}$/);
      expect(observation.runtime_id).toMatch(/^[0-9a-f-]{36}$/);
      expect(observation.occurred_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(observation.monotonic_offset).toBeGreaterThanOrEqual(0);
      expect(observation.route_revision).toBeGreaterThanOrEqual(1);
      expect(["visible", "hidden"]).toContain(observation.visibility_state);
    }
  });
});

describe("interaction, input, and redaction", () => {
  it("records one activation per completed user action, with its trace", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    const before = harness.ingest.observations.filter((o) => o.observation_type === "interaction.activation").length;
    await harness.page.click("#save-view");
    const activations = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "interaction.activation"),
      (found) => found.length > before,
      10_000,
      "an interaction activation record",
    );
    const latest = activations.at(-1);
    expect(latest?.payload["actor"]).toBe("user");
    expect(latest?.payload["activation_kind"]).toBe("pointer");
    expect(latest?.payload["role"]).toBe("button");
    expect(latest?.payload["surface"]).toBe("conversion_dashboard");
    // The button names a signed safe-value rule, so its accessible label passes
    // as a bounded safe value rather than defaulting to metadata only.
    expect(latest?.payload["label"]).toMatchObject({ output_class: "bounded_safe_value", value: "Save view" });
    expect(latest?.trace_id).toMatch(/^[0-9a-f]{32}$/);
    expect(latest?.target_id).toBeDefined();
  });

  it("reports approved input metadata and never the value of a sensitive field", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    await harness.page.fill("#view-name", "TikTok sizing drop-off");
    await harness.page.click("#mutate");
    await harness.page.fill("#billing-card", "4242424242424242");
    await harness.page.click("#mutate");
    const records = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "input.metadata"),
      (found) => found.length >= 2,
      10_000,
      "input metadata for both fields",
    );
    const approved = records.find((record) => record.payload["sensitive_field"] === false);
    const sensitive = records.find((record) => record.payload["sensitive_field"] === true);
    expect(approved?.payload["approved_value"]).toMatchObject({
      output_class: "bounded_safe_value",
      value: "TikTok sizing drop-off",
    });
    expect(approved?.payload["length_bucket"]).toBe("len_11_30");
    // A sensitive field has no approved value, so the key is absent rather than null.
    expect(sensitive?.payload).not.toHaveProperty("approved_value");
    const serialized = JSON.stringify(harness.ingest.observations);
    expect(serialized).not.toContain("4242424242424242");
    expect(serialized).not.toContain("maya@dudulemon.example");
  });

  it("accepts an annotated product milestone and rejects an unregistered one", async () => {
    const accepted = await harness.page.evaluate(() => window.__utraceTest?.milestone("TikTok sizing drop-off"));
    expect(accepted).toBe("accepted");
    const milestone = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "product.milestone"),
      (found) => found.length >= 1,
      10_000,
      "the product milestone record",
    );
    expect(milestone.at(-1)?.payload["milestone_id"]).toBe("saved_view_created");
    const rejected = await harness.page.evaluate(() =>
      window.__utraceTest?.milestone("a name that the signed pattern refuses: <script>"),
    );
    expect(rejected).toBe("accepted");
    const latest = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "product.milestone"),
      (found) => found.length >= 2,
      10_000,
      "the second milestone record",
    );
    const attributes = latest.at(-1)?.payload["attributes"] as { value: { output_class: string } }[];
    expect(attributes[0]?.value.output_class).toBe("metadata_only");
  });
});

describe("request observation and trace propagation scope", () => {
  it("injects trace context on same-origin requests exactly once", async () => {
    await harness.page.click("#same-origin-request");
    const echoed = await waitFor(
      () => harness.controlPlane.requests.filter((request) => request.path === "/api/echo"),
      (found) => found.length >= 1,
      10_000,
      "the same-origin product request",
    );
    expect(echoed[0]?.traceparent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
    expect(echoed[0]?.traceparent_count).toBe(1);
    expect(echoed[0]?.tracestate).toBe("utrace=1");
  });

  it("never injects trace context toward an unapproved third party", async () => {
    await harness.page.click("#third-party-request");
    const echoed = await waitFor(
      () => harness.thirdPartyRequests,
      (found) => found.length >= 1,
      10_000,
      "the third-party request",
    );
    expect(echoed[0]?.traceparent).toBeNull();
    expect(echoed[0]?.tracestate).toBeNull();
  });

  it("pairs started and completed records for fetch and XHR", async () => {
    await harness.page.click("#xhr-request");
    const started = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "request.started"),
      (found) => found.some((record) => record.payload["transport"] === "xhr"),
      10_000,
      "an XHR request.started record",
    );
    const completed = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "request.completed"),
      (found) => found.some((record) => record.payload["transport"] === "xhr"),
      10_000,
      "an XHR request.completed record",
    );
    const xhrStarted = started.find((record) => record.payload["transport"] === "xhr");
    const xhrCompleted = completed.find((record) => record.payload["transport"] === "xhr");
    expect(xhrCompleted?.payload["browser_request_id"]).toBe(xhrStarted?.payload["browser_request_id"]);
    expect(xhrCompleted?.payload["status_code"]).toBe(200);
    expect(xhrCompleted?.payload["route"]).toMatchObject({ unknown_route: true, has_query: false });
    const serialized = JSON.stringify(harness.ingest.observations);
    expect(serialized).not.toContain("/api/echo");
  });
});

describe("flow control and gap reporting", () => {
  it("sheds droppable records under sustained pressure and reports one explicit gap", async () => {
    harness.ingest.setFlowControl("pause");
    for (let index = 0; index < 12; index += 1) {
      await harness.page.click("#mutate");
      await sleep(60);
    }
    await sleep(600);
    harness.ingest.setFlowControl("resume");
    const gaps = await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "observation.gap"),
      (found) => found.length >= 1,
      15_000,
      "an explicit observation gap record",
    );
    const gap = gaps[0];
    expect(gap?.payload["reason"]).toBe("flow_control");
    expect(gap?.payload["observation_class"]).toBe("normal");
    expect(gap?.payload["count"]).toBeGreaterThanOrEqual(1);
    expect(Number(gap?.payload["gap_last_source_sequence"])).toBeGreaterThanOrEqual(
      Number(gap?.payload["gap_first_source_sequence"]),
    );
    await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "baseline.snapshot"),
      (found) => found.some((record) => record.payload["reason"] === "delivery_gap"),
      15_000,
      "the fresh baseline that follows a gap",
    );
  });

  it("never drops a critical record", () => {
    const gaps = harness.ingest.observations.filter((o) => o.observation_type === "observation.gap");
    for (const gap of gaps) expect(gap.payload["observation_class"]).not.toBe("critical");
  });
});
