"use client";

import {
  AgentCharacter,
  CharacterProjectionError,
  acceptCharacterProjection,
  createAgentCharacterResources,
  type AcceptedCharacterProjection,
  type AgentCharacterResources,
} from "@utrace/agent-character";
import { useEffect, useRef, useState, type JSX } from "react";

import type { SessionProjection } from "@/lib/api/schemas";

const WASM_URL = "/agent/rive.wasm";

type AgentColour = SessionProjection["agent_character"];

const RESOURCES: Readonly<Record<AgentColour, AgentCharacterResources>> = Object.freeze({
  coral: createAgentCharacterResources("/agent/utrace-agent-coral.riv", WASM_URL),
  black: createAgentCharacterResources("/agent/utrace-agent-black.riv", WASM_URL),
  mint: createAgentCharacterResources("/agent/utrace-agent-mint.riv", WASM_URL),
  sky: createAgentCharacterResources("/agent/utrace-agent-sky.riv", WASM_URL),
  lilac: createAgentCharacterResources("/agent/utrace-agent-lilac.riv", WASM_URL),
  peach: createAgentCharacterResources("/agent/utrace-agent-peach.riv", WASM_URL),
  aqua: createAgentCharacterResources("/agent/utrace-agent-aqua.riv", WASM_URL),
});

/**
 * The agent on the session tab.
 *
 * The rig is driven only by the control plane's character projection, and the
 * packaged contract decides what to apply: a lower revision is stale and a
 * conflicting one is an error. The page never animates a state it inferred for
 * itself.
 */
export function SessionCharacter(
  props: Readonly<{
    colour: AgentColour;
    projection: SessionProjection["character"];
    size: number;
    onError: (message: string) => void;
  }>,
): JSX.Element {
  const [accepted, setAccepted] = useState<AcceptedCharacterProjection>(
    () => acceptCharacterProjection(null, props.projection).projection,
  );
  const lastAccepted = useRef<AcceptedCharacterProjection>(accepted);
  const { onError, projection } = props;

  useEffect(() => {
    try {
      const decision = acceptCharacterProjection(lastAccepted.current, projection);
      if (decision.kind !== "applied") {
        return;
      }
      lastAccepted.current = decision.projection;
      setAccepted(decision.projection);
    } catch (error) {
      if (error instanceof CharacterProjectionError) {
        onError(`The agent's state could not be applied: ${error.message}`);
        return;
      }
      throw error;
    }
  }, [projection, onError]);

  return (
    <AgentCharacter
      dataTestId="session-character"
      onError={(failure) => onError(failure.message)}
      projection={accepted}
      resources={RESOURCES[props.colour]}
      size={props.size}
    />
  );
}
