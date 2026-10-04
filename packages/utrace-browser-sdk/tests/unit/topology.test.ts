/**
 * Document topology: instrumented child frames and dedicated worker capabilities.
 */

import { describe, expect, it, vi } from "vitest";
import { webCryptoRandom } from "../../src/core/ids.js";
import { FrameCoordinator, FRAME_HELLO_KIND, connectToCoordinator } from "../../src/runtime/frames.js";
import { WORKER_CAPABILITY_KIND, WorkerCapabilityGrants, type DedicatedWorkerLike, type WorkerCapability } from "../../src/runtime/workers.js";

const random = webCryptoRandom(globalThis.crypto);

const coordinator = (
  onRejected: (reason: string) => void,
): { readonly instance: FrameCoordinator; readonly stop: () => void } => {
  const instance = new FrameCoordinator(window, globalThis.crypto.subtle, "1", { client_id: "client-1" }, {
    onChildObservation: () => undefined,
    onAttachmentLost: () => undefined,
    onRejected,
  });
  return { instance, stop: instance.listen() };
};

const deliver = (data: string): void => {
  const channel = new MessageChannel();
  window.dispatchEvent(
    new MessageEvent("message", { data, origin: window.location.origin, ports: [channel.port2] }),
  );
};

const settle = async (): Promise<void> => {
  await new Promise<void>((done) => setTimeout(done, 10));
};

describe("frame coordinator", () => {
  it("refuses to listen twice in one document", () => {
    const { instance, stop } = coordinator(() => undefined);
    expect(() => instance.listen()).toThrow(/already listening/);
    stop();
  });

  it("rejects a child speaking another schema major", async () => {
    const rejections: string[] = [];
    const { stop } = coordinator((reason) => rejections.push(reason));
    deliver(
      JSON.stringify({
        kind: FRAME_HELLO_KIND,
        schema_version: "2",
        frame_id: "frame-1",
        runtime_id: "child-runtime-1",
        attestation: { algorithm: "ed25519", key_id: "k", payload_b64url: "a", signature_b64url: "b" },
      }),
    );
    await settle();
    expect(rejections[0]).toContain("frame speaks schema 2");
    stop();
  });

  it("rejects an attestation signed by an unpackaged key", async () => {
    const rejections: string[] = [];
    const { instance, stop } = coordinator((reason) => rejections.push(reason));
    deliver(
      JSON.stringify({
        kind: FRAME_HELLO_KIND,
        schema_version: "1",
        frame_id: "frame-1",
        runtime_id: "child-runtime-1",
        attestation: {
          algorithm: "ed25519",
          key_id: "some-other-key",
          payload_b64url: "eyJhIjoxfQ",
          signature_b64url: "A".repeat(86),
        },
      }),
    );
    await settle();
    expect(rejections[0]).toContain("frame attestation rejected");
    expect(instance.attachmentCount).toBe(0);
    stop();
  });

  it("ignores an undecodable message and a message with no port", async () => {
    const rejections: string[] = [];
    const { stop } = coordinator((reason) => rejections.push(reason));
    deliver("not json");
    window.dispatchEvent(new MessageEvent("message", { data: "{}", origin: window.location.origin }));
    await settle();
    expect(rejections).toHaveLength(1);
    expect(rejections[0]).toContain("undecodable frame hello");
    stop();
  });

  it("refuses to offer a top document to itself as a child", () => {
    expect(() =>
      connectToCoordinator(window, "frame-1", "child-runtime-1", "1", {
        algorithm: "ed25519",
        key_id: "k",
        payload_b64url: "a",
        signature_b64url: "b",
      }),
    ).toThrow(/not a child frame/);
  });
});

describe("dedicated worker capability", () => {
  const grants = (): WorkerCapabilityGrants =>
    new WorkerCapabilityGrants("runtime-1", "attachment-1", "1", random);

  const worker = (): DedicatedWorkerLike & { readonly messages: WorkerCapability[] } => {
    const messages: WorkerCapability[] = [];
    return { messages, postMessage: vi.fn((message: WorkerCapability) => messages.push(message)) };
  };

  it("grants one capability carrying only runtime and attachment identity", () => {
    const target = worker();
    const capability = grants().grant(target, "2026-10-03T09:15:00.000Z");
    expect(capability.kind).toBe(WORKER_CAPABILITY_KIND);
    expect(capability.capability_id).toMatch(/^wcap_1_[0-9a-f]{16}$/);
    expect(capability.runtime_id).toBe("runtime-1");
    expect(Object.keys(capability).sort()).toEqual([
      "capability_id",
      "document_attachment_id",
      "expires_at",
      "kind",
      "runtime_id",
      "schema_version",
    ]);
    expect(target.messages[0]).toEqual(capability);
  });

  it("refuses a second grant to the same worker", () => {
    const registry = grants();
    const target = worker();
    registry.grant(target, "2026-10-03T09:15:00.000Z");
    expect(() => registry.grant(target, "2026-10-03T09:15:00.000Z")).toThrow(/already holds a uTrace capability/);
  });

  it("keeps a private port per capability and revokes them together", () => {
    const registry = grants();
    const capability = registry.grant(worker(), "2026-10-03T09:15:00.000Z");
    expect(registry.portFor(capability.capability_id)).not.toBeNull();
    expect(registry.portFor("wcap_unknown")).toBeNull();
    expect(registry.grantedCount).toBe(1);
    registry.revokeAll();
    expect(registry.grantedCount).toBe(0);
  });
});
