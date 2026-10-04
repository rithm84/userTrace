import { describe, expect, it } from "vitest";

import {
  EMPTY_COMMAND_REGISTRY,
  reduceCommands,
  type CommandContext,
  type CommandEffect,
  type CommandRegistry,
} from "../../src/background/commandState";
import { SCHEMA_VERSION } from "../../src/protocol/common";
import type { VisualCommandFrame } from "../../src/protocol/control";

const BINDING_ID = "0199a0c0-0000-7aaa-8000-0000000b1d01";
const ATTACHMENT_ID = "0199a0c0-0000-7aaa-8000-0000000a7701";
const OTHER_ATTACHMENT_ID = "0199a0c0-0000-7aaa-8000-0000000a7702";
const COMMAND_ID = "0199a0c0-0000-7aaa-8000-0000000cd001";
const SECOND_COMMAND_ID = "0199a0c0-0000-7aaa-8000-0000000cd002";
const NOW = Date.parse("2026-10-03T17:04:10Z");
const DEADLINE = "2026-10-03T17:04:20Z";

function context(overrides: Partial<CommandContext>): CommandContext {
  return {
    extensionBindingId: BINDING_ID,
    documentAttachmentId: ATTACHMENT_ID,
    tabActive: true,
    combinedReady: true,
    paused: false,
    visualGuidanceEnabled: true,
    now: NOW,
    ...overrides,
  };
}

function command(overrides: Partial<VisualCommandFrame["payload"]>): VisualCommandFrame {
  return {
    schema_version: SCHEMA_VERSION,
    kind: "visual_command",
    message_id: "0199a0c0-0000-7aaa-8000-000000000006",
    sent_at: "2026-10-03T17:04:10Z",
    sequence: 17,
    payload: {
      command_id: COMMAND_ID,
      client_id: "0199a0c0-0000-7aaa-8000-00000000c101",
      session_id: "0199a0c0-0000-7aaa-8000-0000000055e1",
      extension_binding_id: BINDING_ID,
      document_attachment_id: ATTACHMENT_ID,
      expected_lifecycle_revision: 22,
      deadline_at: DEADLINE,
      command: {
        type: "scene",
        scene: {
          scene_id: "0199a0c0-0000-7aaa-8000-000000005ce1",
          steps: [
            { step: "point", target_id: "tgt_save_view_button", target_revision: 7, dwell_ms: 600 },
          ],
        },
      },
      ...overrides,
    },
  };
}

function route(
  registry: CommandRegistry,
  frame: VisualCommandFrame,
  overrides: Partial<CommandContext>,
): { registry: CommandRegistry; effects: readonly CommandEffect[] } {
  return reduceCommands(registry, {
    type: "command_received",
    frame,
    context: context(overrides),
  });
}

describe("command admission", () => {
  it("routes a valid command to the bound surface", () => {
    const transition = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    expect(transition.effects).toStrictEqual([{ type: "route_command", frame: command({}) }]);
    expect(transition.registry.pending.has(COMMAND_ID)).toBe(true);
  });

  it.each([
    ["tab_not_active", { tabActive: false }, "failed"],
    ["session_paused", { paused: true }, "rejected"],
    ["combined_readiness_blocked", { combinedReady: false }, "rejected"],
    ["visual_guidance_disabled", { visualGuidanceEnabled: false }, "rejected"],
    ["binding_mismatch", { extensionBindingId: OTHER_ATTACHMENT_ID }, "rejected"],
    ["no_active_binding", { extensionBindingId: null }, "rejected"],
    ["document_attachment_replaced", { documentAttachmentId: OTHER_ATTACHMENT_ID }, "stale_target"],
  ])("refuses with %s without queueing the command", (reason, overrides, outcome) => {
    const transition = route(EMPTY_COMMAND_REGISTRY, command({}), overrides);
    expect(transition.registry.pending.size).toBe(0);
    expect(transition.effects).toHaveLength(1);
    expect(transition.effects[0]).toMatchObject({
      type: "emit_result",
      result: { command_id: COMMAND_ID, outcome, reason },
    });
  });

  it("times out a command whose deadline already passed", () => {
    const transition = route(EMPTY_COMMAND_REGISTRY, command({}), {
      now: Date.parse("2026-10-03T17:04:30Z"),
    });
    expect(transition.effects[0]).toMatchObject({
      type: "emit_result",
      result: { outcome: "timed_out", reason: "deadline_already_elapsed" },
    });
  });
});

describe("duplicate protection", () => {
  it("replays the canonical terminal result for a repeated command id", () => {
    const routed = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    const completed = reduceCommands(routed.registry, {
      type: "surface_result",
      commandId: COMMAND_ID,
      outcome: "applied",
      observedTargets: [{ target_id: "tgt_save_view_button", target_revision: 7 }],
      now: NOW + 500,
    });

    const repeated = route(completed.registry, command({}), {});
    expect(repeated.effects).toStrictEqual(completed.effects);
    expect(repeated.registry.pending.size).toBe(0);
  });

  it("ignores a repeated command that is still in flight", () => {
    const routed = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    const repeated = route(routed.registry, command({}), {});
    expect(repeated.effects).toStrictEqual([]);
    expect(repeated.registry.pending.size).toBe(1);
  });
});

describe("cancellation, deadlines, and withdrawal", () => {
  it("cancels a routed command and withdraws it from the surface", () => {
    const routed = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    const canceled = reduceCommands(routed.registry, {
      type: "cancel_received",
      commandId: COMMAND_ID,
      reason: "superseded_by_newer_scene",
      now: NOW + 100,
    });
    expect(canceled.effects[0]).toStrictEqual({
      type: "withdraw_command",
      commandId: COMMAND_ID,
      reason: "superseded_by_newer_scene",
    });
    expect(canceled.effects[1]).toMatchObject({
      type: "emit_result",
      result: { outcome: "canceled", reason: "superseded_by_newer_scene" },
    });
  });

  it("supersedes a pending command when a newer one arrives", () => {
    const first = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    const second = route(first.registry, command({ command_id: SECOND_COMMAND_ID }), {});

    expect(second.effects[0]).toStrictEqual({
      type: "withdraw_command",
      commandId: COMMAND_ID,
      reason: "superseded_by_newer_command",
    });
    expect(second.effects[1]).toMatchObject({
      type: "emit_result",
      result: { command_id: COMMAND_ID, outcome: "canceled", reason: "superseded_by_newer_command" },
    });
    expect(second.effects[2]).toStrictEqual({
      type: "route_command",
      frame: command({ command_id: SECOND_COMMAND_ID }),
    });
    expect([...second.registry.pending.keys()]).toStrictEqual([SECOND_COMMAND_ID]);
  });

  it("times out a pending command once its deadline elapses", () => {
    const routed = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    const expired = reduceCommands(routed.registry, {
      type: "deadline_check",
      now: Date.parse(DEADLINE) + 1,
    });
    expect(expired.effects).toHaveLength(2);
    expect(expired.effects[1]).toMatchObject({
      type: "emit_result",
      result: { outcome: "timed_out", reason: "deadline_elapsed" },
    });
    expect(expired.registry.pending.size).toBe(0);
  });

  it("clears scenes and cancels what is pending when pause withdraws authority", () => {
    const routed = route(EMPTY_COMMAND_REGISTRY, command({}), {});
    const paused = reduceCommands(routed.registry, {
      type: "authority_withdrawn",
      reason: "session_paused",
      now: NOW + 200,
    });

    expect(paused.effects[0]).toStrictEqual({ type: "clear_scenes", reason: "session_paused" });
    expect(paused.registry.pending.size).toBe(0);
    const outcomes = paused.effects
      .filter((effect) => effect.type === "emit_result")
      .map((effect) => effect.result.outcome);
    expect(outcomes).toStrictEqual(["canceled"]);
  });
});
