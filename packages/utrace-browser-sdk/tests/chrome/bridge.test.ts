/**
 * Real-Chrome proof of the extension bridge and readiness transitions.
 *
 * The fixture plays the extension's main-world bridge exactly as the packaged
 * extension does: it creates the `MessageChannel`, posts the `page_proof`
 * request on `window` with one port transferred, and then exchanges
 * `bridge-frame.v1` frames — `page_proof`, `target_query` answered by
 * `target_geometry`, `detail_query`, and the one-way `pause_notice`.
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";
import { settle, startHarness, waitFor, waitForReady, type Harness } from "./support/harness.js";

let harness: Harness;

const ATTACHMENT_PLACEHOLDER = "00000000-0000-7000-8000-000000000000";

interface ResolvedTarget {
  readonly target_id: string;
  readonly target_revision: number;
  readonly layout_revision: number;
  readonly frame_id: string;
  readonly rect: { readonly x: number; readonly y: number; readonly width: number; readonly height: number };
  readonly visible_fraction: number;
  readonly occluded: boolean;
  readonly in_viewport: boolean;
  readonly supports_visual_guidance: boolean;
}

interface GeometryPayload {
  readonly document_attachment_id: string;
  readonly registry_revision: number;
  readonly viewport: { readonly width: number; readonly height: number };
  readonly targets: readonly ResolvedTarget[];
  readonly not_found: readonly string[];
  readonly ambiguous: readonly string[];
}

const queryByDefinition = async (definitionId: string): Promise<GeometryPayload> => {
  const reply = await harness.page.evaluate(async (id: string) => {
    const surface = window.__utraceTest;
    if (surface === undefined) throw new Error("fixture is not ready");
    return surface.bridge.send("target_query", "request", {
      document_attachment_id: surface.health().document_attachment_id,
      query: { by: "definition", definition_id: id, effect: "any", visibility: "any" },
      deadline_ms: 400,
    });
  }, definitionId);
  return reply.body as unknown as GeometryPayload;
};

beforeAll(async () => {
  harness = await startHarness({ maxObservationBytes: MAX_OBSERVATION_BYTES, path: "/dashboard/conversions" });
  await waitForReady(harness.page);
  await harness.page.evaluate(async () => {
    await window.__utraceTest?.bridge.connect();
  });
}, 120_000);

afterAll(async () => {
  await harness.stop();
});

describe("extension bridge", () => {
  it("answers the page-proof handshake on the port the extension hands over", async () => {
    const proof = await harness.page.evaluate(async () => {
      const surface = window.__utraceTest;
      if (surface === undefined) throw new Error("fixture is not ready");
      return surface.bridge.connect();
    });
    expect(proof.kind).toBe("page_proof");
    const envelope = proof.body["page_proof"] as { algorithm: string; key_id: string; signature_b64url: string };
    expect(envelope.algorithm).toBe("ed25519");
    expect(envelope.signature_b64url.length).toBe(86);
    expect(proof.body["document_attachment_id"]).toMatch(/^[0-9a-f-]{36}$/u);

    const health = await harness.page.evaluate(() => window.__utraceTest?.health());
    expect(health?.state).toBe("ready");
    expect(proof.body["document_attachment_id"]).toBe(health?.document_attachment_id);
  });

  it("answers a structured target query and never guesses a nearest match", async () => {
    const found = await queryByDefinition("saved_view_save_button");
    expect(found.targets).toHaveLength(1);
    expect(found.not_found).toEqual([]);
    expect(found.viewport.width).toBeGreaterThan(0);

    const missing = await queryByDefinition("a_definition_that_is_not_registered");
    expect(missing.targets).toEqual([]);
    expect(missing.not_found).toEqual(["a_definition_that_is_not_registered"]);
  });

  it("keeps answering the content script after a separate page-proof probe closes its own port", async () => {
    // The service worker probes a bound tab whenever it gains focus, which every
    // scene causes; that probe must not take over the long-lived bridge.
    const probed = await harness.page.evaluate(
      () =>
        new Promise<string>((resolve) => {
          const channel = new MessageChannel();
          channel.port1.addEventListener("message", (message: MessageEvent) => {
            channel.port1.close();
            resolve((message.data as { kind: string }).kind);
          });
          channel.port1.start();
          window.postMessage(
            {
              schema_version: 1,
              kind: "page_proof",
              phase: "request",
              exchange_id: crypto.randomUUID(),
              payload: { extension_protocol_version: "1.0" },
            },
            window.location.origin,
            [channel.port2],
          );
        }),
    );
    expect(probed).toBe("page_proof");

    const answered = await harness.page.evaluate(async () => {
      const surface = window.__utraceTest;
      if (surface === undefined) throw new Error("fixture is not ready");
      const reply = surface.bridge.send("target_query", "request", {
        document_attachment_id: surface.health().document_attachment_id,
        query: { by: "definition", definition_id: "saved_view_save_button", effect: "any", visibility: "any" },
        deadline_ms: 400,
      });
      const timeout = new Promise<null>((resolve) => {
        setTimeout(() => {
          resolve(null);
        }, 2_000);
      });
      return (await Promise.race([reply, timeout]))?.kind ?? "no answer";
    });
    expect(answered).toBe("target_geometry");
  });

  it("supplies clipped top-level geometry, occlusion, and visible fraction by target id", async () => {
    const targetId = (await queryByDefinition("saved_view_save_button")).targets[0]?.target_id;
    expect(targetId).toBeDefined();

    const refreshed = await harness.page.evaluate(async (id: string) => {
      const surface = window.__utraceTest;
      if (surface === undefined) throw new Error("fixture is not ready");
      return surface.bridge.send("target_query", "request", {
        document_attachment_id: surface.health().document_attachment_id,
        query: { by: "target_ids", target_ids: [id, "tgt_never_mounted"] },
        deadline_ms: 400,
      });
    }, String(targetId));

    const payload = refreshed.body as unknown as GeometryPayload;
    const target = payload.targets[0];
    expect(target?.target_id).toBe(targetId);
    expect(target?.rect.width).toBeGreaterThan(0);
    expect(target?.in_viewport).toBe(true);
    expect(target?.occluded).toBe(false);
    expect(target?.visible_fraction).toBe(1);
    expect(target?.supports_visual_guidance).toBe(true);
    expect(payload.not_found).toEqual(["tgt_never_mounted"]);
  });

  it("lets the agent show a disabled control, such as a save button waiting for a name", async () => {
    const targetId = (await queryByDefinition("saved_view_save_button")).targets[0]?.target_id;
    expect(targetId).toBeDefined();

    const resolved = await harness.page.evaluate(async (id: string) => {
      const surface = window.__utraceTest;
      if (surface === undefined) throw new Error("fixture is not ready");
      const button = document.querySelector<HTMLButtonElement>('[data-utrace-target="saved_view_save_button"]');
      if (button === null) throw new Error("the fixture has no save button");
      button.disabled = true;
      try {
        return await surface.bridge.send("target_query", "request", {
          document_attachment_id: surface.health().document_attachment_id,
          query: { by: "target_ids", target_ids: [id] },
          deadline_ms: 400,
        });
      } finally {
        button.disabled = false;
      }
    }, String(targetId));

    const target = (resolved.body as unknown as GeometryPayload).targets[0];
    expect(target?.target_id).toBe(targetId);
    expect(target?.supports_visual_guidance).toBe(true);
  });

  it("answers an authorized detail query and refuses an unsupported or sensitive field", async () => {
    const context = await harness.page.evaluate(async () => {
      const surface = window.__utraceTest;
      if (surface === undefined) throw new Error("fixture is not ready");
      const health = surface.health();
      const reply = await surface.bridge.send("target_query", "request", {
        document_attachment_id: health.document_attachment_id,
        query: {
          by: "definition",
          definition_id: "saved_view_save_button",
          effect: "any",
          visibility: "any",
        },
        deadline_ms: 400,
      });
      const payload = reply.body as { registry_revision: number; targets: { target_id: string }[] };
      return {
        registryRevision: payload.registry_revision,
        targetId: payload.targets[0]?.target_id ?? "",
        documentAttachmentId: health.document_attachment_id,
      };
    });

    const detail = (
      fields: readonly string[],
      revision: number,
    ): Promise<{ kind: string; body: Record<string, unknown> }> =>
      harness.page.evaluate(
        async (input: { fields: string[]; revision: number; targetId: string; attachment: string }) => {
          const surface = window.__utraceTest;
          if (surface === undefined) throw new Error("fixture is not ready");
          return surface.bridge.send("detail_query", "request", {
            document_attachment_id: input.attachment,
            expected_registry_revision: input.revision,
            target_id: input.targetId,
            fields: input.fields,
            depth_limit: 1,
            size_limit_bytes: 8192,
            deadline_ms: 5000,
          });
        },
        {
          fields: [...fields],
          revision,
          targetId: context.targetId,
          attachment: context.documentAttachmentId,
        },
      );

    const ok = await detail(["role", "label", "semantic_state"], context.registryRevision);
    expect(ok.body["disposition"]).toBe("resolved");
    expect((ok.body["fields"] as Record<string, unknown>)["role"]).toBe("button");
    expect(ok.body["target_revision"]).toBeGreaterThan(0);

    const sensitive = await detail(["value"], context.registryRevision);
    expect(sensitive.body).toEqual({ disposition: "failed", reason: "sensitive_field" });

    const unsupported = await detail(["computed_style"], context.registryRevision);
    expect(unsupported.body).toEqual({ disposition: "failed", reason: "unsupported_field" });

    const stale = await detail(["role"], context.registryRevision + 500);
    expect(stale.body).toEqual({ disposition: "failed", reason: "registry_revision_mismatch" });

    // A detail query addressed to another document attachment is inactive, not
    // an opportunity to answer about this one.
    const foreign = await harness.page.evaluate(
      async (attachment: string) =>
        window.__utraceTest?.bridge.send("detail_query", "request", {
          document_attachment_id: attachment,
          expected_registry_revision: 1,
          target_id: "tgt_1_a",
          fields: ["role"],
          depth_limit: 1,
          size_limit_bytes: 1024,
          deadline_ms: 5000,
        }),
      ATTACHMENT_PLACEHOLDER,
    );
    expect(foreign?.body).toEqual({ disposition: "failed", reason: "inactive_attachment" });
  });
});

describe("pause and resume", () => {
  it("stops session-correlated export on pause and resumes the same stream with its unexpired credential", async () => {
    await settle(() => harness.ingest.observations.length, 400, 15_000);
    const pausedStream = harness.ingest.streamIds.at(-1);
    await harness.page.evaluate(() => {
      window.__utraceTest?.bridge.notify({
        session_id: "0199a0c0-0000-7aaa-8000-0000000055e1",
        document_attachment_id: window.__utraceTest.health().document_attachment_id,
        pause_state: "paused",
        session_control_revision: 4,
      });
    });
    await waitFor(
      async () => (await harness.page.evaluate(() => window.__utraceTest?.health().state)) ?? "unknown",
      (state) => state === "paused",
      15_000,
      "the runtime to reach paused",
    );
    const pausedEnd = harness.ingest.streamEnds.find((end) => end.reason === "paused");
    expect(pausedEnd).toBeDefined();

    // Pause completes only after acknowledged cleanup, so the count is taken
    // once the transition is done: nothing may arrive after it.
    const beforePause = harness.ingest.observations.length;
    await harness.page.click("#mutate");
    await harness.page.click("#save-view");
    await new Promise<void>((done) => setTimeout(done, 700));
    expect(harness.ingest.observations.length).toBe(beforePause);

    const activationsBefore = harness.controlPlane.activationCount();
    await harness.page.evaluate(() => {
      window.__utraceTest?.bridge.notify({
        session_id: "0199a0c0-0000-7aaa-8000-0000000055e1",
        document_attachment_id: window.__utraceTest.health().document_attachment_id,
        pause_state: "running",
        session_control_revision: 5,
      });
    });
    await waitFor(
      async () => (await harness.page.evaluate(() => window.__utraceTest?.health().state)) ?? "unknown",
      (state) => state === "ready",
      15_000,
      "the runtime to return to ready",
    );
    expect(harness.controlPlane.activationCount()).toBe(activationsBefore);
    expect(harness.ingest.streamIds.at(-1)).toBe(pausedStream);
    expect(harness.ingest.resumeRequests.at(-1)).toBe(pausedEnd?.terminal_export_sequence);

    const health = await harness.page.evaluate(() => window.__utraceTest?.health());
    expect(Object.values(health?.components ?? {}).every(Boolean)).toBe(true);

    await waitFor(
      () => harness.ingest.observations.filter((o) => o.observation_type === "baseline.snapshot"),
      (found) => found.some((record) => record.payload["reason"] === "resume"),
      15_000,
      "a fresh baseline after resume",
    );
  });
});
