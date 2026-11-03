export const AGENT_HEX = Object.freeze({
  coral: "#FCADA3",
  black: "#111111",
  mint: "#97D6A4",
  sky: "#85CEF9",
  lilac: "#CDB6F9",
  peach: "#F5B488",
  aqua: "#72D8D7",
} as const);
export type AgentColour = keyof typeof AGENT_HEX;
