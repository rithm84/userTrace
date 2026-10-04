import type { JSX } from "react";
import { vi } from "vitest";

export const configureAgentCharacterRuntime = vi.fn<(resources: Readonly<{ assetUrl: string; wasmUrl: string }>) => void>();

export const CHARACTER_STATES = [
  "idle",
  "investigating",
  "listening",
  "thinking",
  "speaking",
  "acknowledgement",
  "concern",
  "building",
  "completion",
  "ship",
  "reconnecting",
  "paused",
  "sleep",
  "pointer",
] as const;

type Projection = Readonly<{
  revision: number;
  state: (typeof CHARACTER_STATES)[number];
  gazeX: number;
  gazeY: number;
  speechEnergy: number;
  motionScale: number;
}>;

export function acceptCharacterProjection(
  _previous: Projection | null,
  projection: Projection,
) {
  return { kind: "applied" as const, projection };
}

export function createAgentCharacterResources(
  assetUrl: string,
  wasmUrl: string,
) {
  return { assetUrl, wasmUrl };
}

export function AgentCharacter({
  dataTestId,
  projection,
  size,
}: {
  dataTestId: string;
  projection: Projection;
  size: number;
}): JSX.Element {
  return (
    <div
      data-revision={projection.revision}
      data-state={projection.state}
      data-testid={dataTestId}
      style={{ height: size, width: size }}
    />
  );
}
