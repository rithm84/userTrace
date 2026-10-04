"use client";

import {
  AgentCharacter,
  acceptCharacterProjection,
  createAgentCharacterResources,
  type AcceptedCharacterProjection,
  type AgentCharacterFailure,
  type AgentCharacterResources,
  type CharacterState,
} from "@utrace/agent-character";
import { useMemo, useRef, useState, type CSSProperties, type JSX } from "react";

import { useNearViewport } from "./useNearViewport";

import type { AgentColour } from "./AgentSprite";

const WASM_URL = "/agent/rive.wasm";

const RESOURCES: Readonly<Record<AgentColour, AgentCharacterResources>> = Object.freeze({
  coral: createAgentCharacterResources("/agent/utrace-agent-coral.riv", WASM_URL),
  black: createAgentCharacterResources("/agent/utrace-agent-black.riv", WASM_URL),
  mint: createAgentCharacterResources("/agent/utrace-agent-mint.riv", WASM_URL),
  sky: createAgentCharacterResources("/agent/utrace-agent-sky.riv", WASM_URL),
  lilac: createAgentCharacterResources("/agent/utrace-agent-lilac.riv", WASM_URL),
  peach: createAgentCharacterResources("/agent/utrace-agent-peach.riv", WASM_URL),
  aqua: createAgentCharacterResources("/agent/utrace-agent-aqua.riv", WASM_URL),
});

export type LandingAgentProps = Readonly<{
  colour: AgentColour;
  state: CharacterState;
  size: number;
  /** Eye direction, -1..1 on each axis; the rig follows within 12 px. */
  gazeX?: number;
  gazeY?: number;
  /** 0..1, scales the middle speech dot while `speaking`. */
  speechEnergy?: number;
  className?: string;
  style?: CSSProperties;
  testId: string;
}>;

function reportAgentError(error: AgentCharacterFailure): void {
  throw error;
}

/**
 * One of the seven Rive agent rigs driven by plain props. Every prop change
 * becomes a new accepted projection revision (derived state, so the revision
 * only ever climbs), which is the contract the packaged character enforces;
 * the rig itself pauses when its canvas leaves the viewport, and is not even
 * fetched until it comes within a screen of it, so the hero loads first.
 */
export function LandingAgent(props: LandingAgentProps): JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  const near = useNearViewport(host, 1000);
  const gazeX = props.gazeX ?? 0;
  const gazeY = props.gazeY ?? 0;
  const speechEnergy = props.speechEnergy ?? (props.state === "speaking" ? 0.8 : 0);
  const [seen, setSeen] = useState({ state: props.state, gazeX, gazeY, speechEnergy, revision: 1 });
  if (seen.state !== props.state || seen.gazeX !== gazeX || seen.gazeY !== gazeY || seen.speechEnergy !== speechEnergy) {
    setSeen({ state: props.state, gazeX, gazeY, speechEnergy, revision: seen.revision + 1 });
  }
  const projection = useMemo<AcceptedCharacterProjection>(
    () =>
      acceptCharacterProjection(null, {
        revision: seen.revision,
        state: seen.state,
        gazeX: seen.gazeX,
        gazeY: seen.gazeY,
        speechEnergy: seen.speechEnergy,
        motionScale: 1,
      }).projection,
    [seen],
  );

  return (
    <div ref={host} className={props.className} style={{ lineHeight: 0, width: props.size, height: props.size, ...props.style }}>
      {near ? <AgentCharacter
        dataTestId={props.testId}
        onError={reportAgentError}
        projection={projection}
        resources={RESOURCES[props.colour]}
        size={props.size}
      /> : null}
    </div>
  );
}
