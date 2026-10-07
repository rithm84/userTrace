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
