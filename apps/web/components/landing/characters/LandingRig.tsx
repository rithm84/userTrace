"use client";

import { useRive, useViewModelInstanceEnum, useViewModelInstanceNumber } from "@rive-app/react-webgl2";
import { Alignment, Fit, Layout } from "@rive-app/webgl2";
import { configureAgentCharacterRuntime, createAgentCharacterResources } from "@utrace/agent-character";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type JSX } from "react";

import type { AgentColour } from "./AgentSprite";
import { assertLandingPose, type LandingPose } from "./landingPoses";
import { fitRigCanvas } from "./rigCanvas";
import { useNearViewport } from "./useNearViewport";

const WASM_URL = "/agent/rive.wasm";
const RESOURCES: Readonly<Partial<Record<AgentColour, ReturnType<typeof createAgentCharacterResources>>>> = Object.freeze({
  coral: createAgentCharacterResources("/landing/utrace-landing-coral.riv", WASM_URL),
  black: createAgentCharacterResources("/landing/utrace-landing-black.riv", WASM_URL),
  mint: createAgentCharacterResources("/landing/utrace-landing-mint.riv", WASM_URL),
});

export type LandingRigProps = Readonly<{
  colour: AgentColour;
  pose: LandingPose;
  /** Rendered size in CSS px; the canvas is allocated at this size, so scale the wrapper down, never up. */
  size: number;
  /** -1..1 on each axis; the eyes follow within 12 px. */
  gazeX?: number;
  gazeY?: number;
  /** 0..1, a body pulse for speech; the rig documents whether it is bound. */
  energy?: number;
  /** False parks the rig: playback paused and rendering stopped. Opacity alone does not. */
  active?: boolean;
  style?: CSSProperties;
  testId: string;
}>;

/**
 * One landing-only Rive rig (utrace-landing-<colour>.riv, artboard LandingAgent,
 * machine LandingMachine, view model LandingModel with the pose enum, gaze and
 * energy). The seven product characters keep their fourteen-state contract in
 * the shared package; these files carry the poses the landing choreographs.
 */
export function LandingRig(props: LandingRigProps): JSX.Element {
  const resources = RESOURCES[props.colour];
  if (resources === undefined) {
    throw new Error(`No landing rig has been authored for the ${props.colour} character.`);
  }
  assertLandingPose(props.colour, props.pose);
  configureAgentCharacterRuntime(resources);
  return (
    <div data-testid={props.testId} data-pose={props.pose} style={{ width: props.size, height: props.size, lineHeight: 0, ...props.style }}>
      <Loaded
        src={resources.assetUrl}
        pose={props.pose}
        gazeX={props.gazeX ?? 0}
        gazeY={props.gazeY ?? 0}
        energy={props.energy ?? 0}
        active={props.active ?? true}
      />
    </div>
  );
}

/** The runtime's backing-store ratio, capped at 2; it only configures the canvas, so the server's 1 never reaches the DOM. */
function useCappedDpr(): number {
  const [dpr] = useState(() => (typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio, 2)));
  return dpr;
}

function Loaded(props: Readonly<{ src: string; pose: LandingPose; gazeX: number; gazeY: number; energy: number; active: boolean }>): JSX.Element {
  const dpr = useCappedDpr();
  const host = useRef<HTMLDivElement>(null);
  // the .riv is fetched only once the rig is within a screen of the viewport, so the hero loads first
  const near = useNearViewport(host, 1000);
  const { rive, RiveComponent } = useRive(
    near ? {
      src: props.src,
      artboard: "LandingAgent",
      stateMachines: "LandingMachine",
      autoplay: true,
      autoBind: true,
      enableRiveAssetCDN: false,
      layout: new Layout({ fit: Fit.Contain, alignment: Alignment.Center }),
    } : null,
    { customDevicePixelRatio: dpr, useDevicePixelRatio: true, useOffscreenRenderer: true, shouldResizeCanvasToContainer: true },
  );
  const instance = rive?.viewModelInstance ?? null;
  // only the setters are kept: the hooks' result objects change on every property event, and an
  // effect keyed on them would write the same value back and re-fire the event without end
  const { setValue: setPose } = useViewModelInstanceEnum("pose", instance);
  const { setValue: setGazeX } = useViewModelInstanceNumber("gazeX", instance);
  const { setValue: setGazeY } = useViewModelInstanceNumber("gazeY", instance);
  const { setValue: setEnergy } = useViewModelInstanceNumber("energy", instance);
  const applied = useRef<{ pose: LandingPose | null; gazeX: number | null; gazeY: number | null; energy: number | null }>({ pose: null, gazeX: null, gazeY: null, energy: null });

  useEffect(() => {
    if (instance === null) {
      applied.current = { pose: null, gazeX: null, gazeY: null, energy: null };
      return;
    }
    if (applied.current.pose !== props.pose) {
      applied.current.pose = props.pose;
      setPose(props.pose);
    }
  }, [instance, setPose, props.pose]);

  useEffect(() => {
    if (instance === null) {
      return;
    }
    if (applied.current.gazeX !== props.gazeX) {
      applied.current.gazeX = props.gazeX;
      setGazeX(props.gazeX);
    }
    if (applied.current.gazeY !== props.gazeY) {
      applied.current.gazeY = props.gazeY;
      setGazeY(props.gazeY);
    }
    if (applied.current.energy !== props.energy) {
      applied.current.energy = props.energy;
      setEnergy(props.energy);
    }
  }, [instance, setGazeX, setGazeY, setEnergy, props.gazeX, props.gazeY, props.energy]);

  // before paint: sizing a new canvas draws the rig's first frame, so it never paints one empty frame first
  useLayoutEffect(() => {
    if (rive === null) {
      return;
    }
    fitRigCanvas(rive, host.current, dpr);
    rive.startRendering();
    rive.play("LandingMachine");
    if (props.active) {
      return;
    }
    // park only after the pose has settled, so a parked rig still shows it
    const settle = window.setTimeout(() => {
      rive.pause("LandingMachine");
      rive.stopRendering();
    }, 1500);
    return () => window.clearTimeout(settle);
  }, [rive, props.active, props.pose, dpr]);

  useEffect(() => {
    if (rive === null) return;
    const resize = (): void => fitRigCanvas(rive, host.current, dpr);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [rive, dpr]);

  return (
    <div ref={host} style={{ width: "100%", height: "100%" }}>
      <RiveComponent aria-hidden="true" style={{ width: "100%", height: "100%" }} />
    </div>
  );
}
