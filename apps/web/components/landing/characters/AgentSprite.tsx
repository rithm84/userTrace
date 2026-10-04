import type { CSSProperties, JSX } from "react";

export const AGENT_COLOURS = [
  "coral",
  "black",
  "mint",
  "sky",
  "lilac",
  "peach",
  "aqua",
] as const;
export type AgentColour = (typeof AGENT_COLOURS)[number];

export const AGENT_HEX: Readonly<Record<AgentColour, string>> = Object.freeze({
  coral: "#FCADA3",
  black: "#111111",
  mint: "#97D6A4",
  sky: "#85CEF9",
  lilac: "#CDB6F9",
  peach: "#F5B488",
  aqua: "#72D8D7",
});

/** The five accessory poses exported from the stage-2 vectors, plus the bare body. */
export type AgentPose =
  | "plain"
  | "listening"
  | "building"
  | "validating"
  | "shipping"
  | "investigating";

export function agentSpriteSrc(colour: AgentColour, pose: AgentPose): string {
  return pose === "plain" ? `/agents/${colour}.svg` : `/agents/${colour}-${pose}.svg`;
}

/**
 * A static character: the exact stage-2 vector (200x200 box, body centred
 * around (100, 118)) as an image. Cheap enough to scatter; the Rive rigs are
 * reserved for characters that act.
 */
export function AgentSprite(props: Readonly<{
  colour: AgentColour;
  pose?: AgentPose;
  size: number;
  flip?: boolean;
  className?: string;
  style?: CSSProperties;
}>): JSX.Element {
  const pose = props.pose ?? "plain";
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static vector; next/image adds nothing
    <img
      alt=""
      aria-hidden="true"
      className={props.className}
      draggable={false}
      height={props.size}
      src={agentSpriteSrc(props.colour, pose)}
      style={{
        display: "block",
        width: props.size,
        height: props.size,
        transform: props.flip ? "scaleX(-1)" : undefined,
        userSelect: "none",
        ...props.style,
      }}
      width={props.size}
    />
  );
}
