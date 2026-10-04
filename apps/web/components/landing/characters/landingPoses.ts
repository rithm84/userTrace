import type { AgentColour } from "./AgentSprite";

/**
 * The landing rigs' pose enum (`LandingModel.pose` in utrace-landing-<colour>.riv).
 * Every rig carries the whole enum; a colour that has not authored a pose
 * plays its idle. The families that share an accessory (coral's headset,
 * coral's deerstalker, black's hard hat) transition directly between their
 * members so the accessory never flickers.
 */
export const LANDING_POSES = [
  "idle",
  "investigate",
  "realise",
  "listen",
  "speak",
  "nod",
  "concern",
  "build",
  "repair",
  "done",
  "present",
] as const;

export type LandingPose = (typeof LANDING_POSES)[number];

/** Which colours have a landing rig, and the poses each one actually performs. */
export const LANDING_RIGS: Readonly<Partial<Record<AgentColour, readonly LandingPose[]>>> = Object.freeze({
  coral: ["idle", "investigate", "realise", "listen", "speak", "nod", "concern"],
  black: ["idle", "build", "repair", "done"],
  mint: ["idle", "present", "nod"],
});

export function assertLandingPose(colour: AgentColour, pose: LandingPose): void {
  const poses = LANDING_RIGS[colour];
  if (poses === undefined) {
    throw new Error(`No landing rig has been authored for the ${colour} character.`);
  }
  if (!poses.includes(pose)) {
    throw new Error(`The ${colour} landing rig does not perform "${pose}".`);
  }
}
