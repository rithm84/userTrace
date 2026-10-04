/**
 * The browser-command lifecycle, owned by the service worker.
 *
 * Commands are validated and checkpointed before they reach a content surface.
 * A repeated `command_id` replays the canonical terminal result instead of
 * repeating an effect, and rebind, pause, cancellation, replacement, or
 * deadline expiry invalidates incompatible pending commands. A command that
 * arrives while the bound tab is inactive fails as `tab_not_active`; it is
 * never queued for later execution.
 */
import type { CommandOutcome, TargetReference } from "../protocol/common";
import type { VisualCommandFrame } from "../protocol/control";

export type CommandResultRecord = Readonly<{
  command_id: string;
  outcome: CommandOutcome;
  reason?: string;
  extension_binding_id: string;
  document_attachment_id: string;
  observed_targets: readonly TargetReference[];
  completed_at: string;
}>;

export type PendingCommand = Readonly<{
  commandId: string;
  frame: VisualCommandFrame;
  deadlineAt: number;
  routedAt: number;
}>;

export type CommandRegistry = Readonly<{
  pending: ReadonlyMap<string, PendingCommand>;
  terminal: ReadonlyMap<string, CommandResultRecord>;
}>;

export const EMPTY_COMMAND_REGISTRY: CommandRegistry = Object.freeze({
  pending: new Map<string, PendingCommand>(),
  terminal: new Map<string, CommandResultRecord>(),
});

/** Everything the reducer needs to decide, gathered by the service worker. */
export type CommandContext = Readonly<{
  extensionBindingId: string | null;
  documentAttachmentId: string | null;
  tabActive: boolean;
  combinedReady: boolean;
  paused: boolean;
  visualGuidanceEnabled: boolean;
  now: number;
}>;

export type CommandEvent =
  | Readonly<{ type: "command_received"; frame: VisualCommandFrame; context: CommandContext }>
  | Readonly<{
      type: "surface_result";
      commandId: string;
      outcome: CommandOutcome;
      reason?: string;
      observedTargets: readonly TargetReference[];
      now: number;
    }>
  | Readonly<{ type: "cancel_received"; commandId: string; reason: string; now: number }>
  | Readonly<{ type: "deadline_check"; now: number }>
  | Readonly<{ type: "authority_withdrawn"; reason: string; now: number }>;

export type CommandEffect =
  | Readonly<{ type: "route_command"; frame: VisualCommandFrame }>
  | Readonly<{ type: "withdraw_command"; commandId: string; reason: string }>
  | Readonly<{ type: "emit_result"; result: CommandResultRecord }>
  | Readonly<{ type: "clear_scenes"; reason: string }>;

export type CommandTransition = Readonly<{
  registry: CommandRegistry;
  effects: readonly CommandEffect[];
}>;

export function reduceCommands(
  registry: CommandRegistry,
  event: CommandEvent,
): CommandTransition {
  switch (event.type) {
    case "command_received":
      return receiveCommand(registry, event.frame, event.context);
    case "surface_result":
      return completeCommand(registry, event);
    case "cancel_received":
      return cancelCommand(registry, event);
    case "deadline_check":
      return expireCommands(registry, event.now);
    case "authority_withdrawn":
      return withdrawAll(registry, event.reason, event.now);
  }
}

function receiveCommand(
  registry: CommandRegistry,
  frame: VisualCommandFrame,
  context: CommandContext,
): CommandTransition {
  const { command_id: commandId } = frame.payload;

  const replayed = registry.terminal.get(commandId);
  if (replayed !== undefined) {
    return { registry, effects: [{ type: "emit_result", result: replayed }] };
  }
  if (registry.pending.has(commandId)) {
    return { registry, effects: [] };
  }

  const rejection = validate(frame, context);
  if (rejection !== null) {
    return terminate(registry, {
      command_id: commandId,
      outcome: rejection.outcome,
      reason: rejection.reason,
      extension_binding_id: frame.payload.extension_binding_id,
      document_attachment_id: frame.payload.document_attachment_id,
      observed_targets: [],
      completed_at: new Date(context.now).toISOString(),
    });
  }

  // A newer command replaces an older one: the surface renders one scene at a
  // time, so the command it supersedes reaches its own terminal result rather
  // than interleaving with it.
  const superseded = supersede(registry, context.now);
  const pending = new Map(superseded.registry.pending);
  pending.set(commandId, {
    commandId,
    frame,
    deadlineAt: Date.parse(frame.payload.deadline_at),
    routedAt: context.now,
  });

  return {
    registry: { pending, terminal: superseded.registry.terminal },
    effects: [...superseded.effects, { type: "route_command", frame }],
  };
}

function supersede(registry: CommandRegistry, now: number): CommandTransition {
  let next = registry;
  const effects: CommandEffect[] = [];

  for (const pending of registry.pending.values()) {
    effects.push({
      type: "withdraw_command",
      commandId: pending.commandId,
      reason: "superseded_by_newer_command",
    });
    const transition = terminate(next, {
      command_id: pending.commandId,
      outcome: "canceled",
      reason: "superseded_by_newer_command",
      extension_binding_id: pending.frame.payload.extension_binding_id,
      document_attachment_id: pending.frame.payload.document_attachment_id,
      observed_targets: [],
      completed_at: new Date(now).toISOString(),
    });
    next = transition.registry;
    effects.push(...transition.effects);
  }

  return { registry: next, effects };
}

type Rejection = Readonly<{ outcome: CommandOutcome; reason: string }>;

function validate(frame: VisualCommandFrame, context: CommandContext): Rejection | null {
  const { payload } = frame;

  if (context.extensionBindingId === null || context.documentAttachmentId === null) {
    return { outcome: "rejected", reason: "no_active_binding" };
  }
  if (payload.extension_binding_id !== context.extensionBindingId) {
    return { outcome: "rejected", reason: "binding_mismatch" };
  }
  if (payload.document_attachment_id !== context.documentAttachmentId) {
    return { outcome: "stale_target", reason: "document_attachment_replaced" };
  }
  if (Number.isNaN(Date.parse(payload.deadline_at))) {
    return { outcome: "rejected", reason: "unreadable_deadline" };
  }
  if (Date.parse(payload.deadline_at) <= context.now) {
    return { outcome: "timed_out", reason: "deadline_already_elapsed" };
  }
  if (context.paused) {
    return { outcome: "rejected", reason: "session_paused" };
  }
  if (!context.combinedReady) {
    return { outcome: "rejected", reason: "combined_readiness_blocked" };
  }
  if (!context.visualGuidanceEnabled) {
    return { outcome: "rejected", reason: "visual_guidance_disabled" };
  }
  if (!context.tabActive) {
    return { outcome: "failed", reason: "tab_not_active" };
  }
  return null;
}

function completeCommand(
  registry: CommandRegistry,
  event: Extract<CommandEvent, { type: "surface_result" }>,
): CommandTransition {
  const pending = registry.pending.get(event.commandId);
  if (pending === undefined) {
    return { registry, effects: [] };
  }

  const result: CommandResultRecord = {
    command_id: event.commandId,
    outcome: event.outcome,
    ...(event.reason === undefined ? {} : { reason: event.reason }),
    extension_binding_id: pending.frame.payload.extension_binding_id,
    document_attachment_id: pending.frame.payload.document_attachment_id,
    observed_targets: event.observedTargets,
    completed_at: new Date(event.now).toISOString(),
  };
  return terminate(registry, result);
}

function cancelCommand(
  registry: CommandRegistry,
  event: Extract<CommandEvent, { type: "cancel_received" }>,
): CommandTransition {
  const replayed = registry.terminal.get(event.commandId);
  if (replayed !== undefined) {
    return { registry, effects: [{ type: "emit_result", result: replayed }] };
  }

  const pending = registry.pending.get(event.commandId);
  if (pending === undefined) {
    return { registry, effects: [] };
  }

  const transition = terminate(registry, {
    command_id: event.commandId,
    outcome: "canceled",
    reason: event.reason,
    extension_binding_id: pending.frame.payload.extension_binding_id,
    document_attachment_id: pending.frame.payload.document_attachment_id,
    observed_targets: [],
    completed_at: new Date(event.now).toISOString(),
  });

  return {
    registry: transition.registry,
    effects: [
      { type: "withdraw_command", commandId: event.commandId, reason: event.reason },
      ...transition.effects,
    ],
  };
}

function expireCommands(registry: CommandRegistry, now: number): CommandTransition {
  let next = registry;
  const effects: CommandEffect[] = [];

  for (const pending of registry.pending.values()) {
    if (pending.deadlineAt > now) {
      continue;
    }
    effects.push({
      type: "withdraw_command",
      commandId: pending.commandId,
      reason: "deadline_elapsed",
    });
    const transition = terminate(next, {
      command_id: pending.commandId,
      outcome: "timed_out",
      reason: "deadline_elapsed",
      extension_binding_id: pending.frame.payload.extension_binding_id,
      document_attachment_id: pending.frame.payload.document_attachment_id,
      observed_targets: [],
      completed_at: new Date(now).toISOString(),
    });
    next = transition.registry;
    effects.push(...transition.effects);
  }

  return { registry: next, effects };
}

function withdrawAll(
  registry: CommandRegistry,
  reason: string,
  now: number,
): CommandTransition {
  let next = registry;
  const effects: CommandEffect[] = [{ type: "clear_scenes", reason }];

  for (const pending of registry.pending.values()) {
    effects.push({ type: "withdraw_command", commandId: pending.commandId, reason });
    const transition = terminate(next, {
      command_id: pending.commandId,
      outcome: "canceled",
      reason,
      extension_binding_id: pending.frame.payload.extension_binding_id,
      document_attachment_id: pending.frame.payload.document_attachment_id,
      observed_targets: [],
      completed_at: new Date(now).toISOString(),
    });
    next = transition.registry;
    effects.push(...transition.effects);
  }

  return { registry: next, effects };
}

function terminate(
  registry: CommandRegistry,
  result: CommandResultRecord,
): CommandTransition {
  const pending = new Map(registry.pending);
  pending.delete(result.command_id);
  const terminal = new Map(registry.terminal);
  terminal.set(result.command_id, result);
  return {
    registry: { pending, terminal },
    effects: [{ type: "emit_result", result }],
  };
}
