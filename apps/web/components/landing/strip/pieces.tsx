"use client";

import { useRive, useViewModelInstanceEnum, useViewModelInstanceNumber } from "@rive-app/react-webgl2";
import { Alignment, Fit, Layout } from "@rive-app/webgl2";
import { configureAgentCharacterRuntime, createAgentCharacterResources, type CharacterState } from "@utrace/agent-character";
import Image, { type ImageProps } from "next/image";
import { useEffect, useLayoutEffect, useRef, type CSSProperties, type JSX, type ReactNode } from "react";

import { AGENT_HEX, type AgentColour } from "../characters/AgentSprite";
import { fitRigCanvas } from "../characters/rigCanvas";
import { useLandingRiveFile } from "../characters/RiveFiles";
import { useNearViewport } from "../characters/useNearViewport";

/** A narration box: white, 2px ink, bold sentence case; pops on when its cue passes. */
export function Caption(props: Readonly<{ on: boolean; children: ReactNode; style?: CSSProperties; testId?: string }>): JSX.Element {
  return (
    <div className={"strip-caption" + (props.on ? " is-on" : "")} data-testid={props.testId} style={props.style}>
      {props.children}
    </div>
  );
}

export type MayaPiece = "desk" | "bust-back" | "hand-mouse";

/** Each drawing of Maya: its source size, and the CSS width the strip draws it at. */
export const MAYA_ART: Readonly<Record<MayaPiece, Readonly<{ natural: readonly [number, number]; width: number }>>> = {
  desk: { natural: [1400, 929], width: 416 },
  "bust-back": { natural: [636, 924], width: 140 },
  "hand-mouse": { natural: [900, 718], width: 330 },
};

/** A drawing's image props, served as alpha WebP through the image optimizer; the strip preloads the same. */
export function mayaArtImage(piece: MayaPiece): ImageProps {
  const { natural, width } = MAYA_ART[piece];
  return { src: `/landing/strip/maya-${piece}.webp`, alt: "", width: natural[0], height: natural[1], sizes: `${Math.ceil(width * 1.3)}px`, quality: 90 };
}

/** Drawn Maya art at its fixed CSS size (`MAYA_ART`). */
export function MayaArt(props: Readonly<{ piece: MayaPiece; style?: CSSProperties }>): JSX.Element {
  const { natural, width } = MAYA_ART[props.piece];
  return (
    <Image
      {...mayaArtImage(props.piece)}
      alt=""
      draggable={false}
      style={{ position: "absolute", width, height: (width * natural[1]) / natural[0], pointerEvents: "none", ...props.style }}
    />
  );
}

/** Two tiny round hands in the body colour, gripping a border line. */
export function Hands(props: Readonly<{ colour: AgentColour; left: number; top: number; gap: number }>): JSX.Element {
  return (
    <>
      {[0, 1].map((index) => (
        <i key={index} aria-hidden="true" style={{ position: "absolute", left: props.left + index * props.gap, top: props.top, width: 15, height: 15, borderRadius: "50%", background: AGENT_HEX[props.colour] }} />
      ))}
    </>
  );
}

/** How long a rig needs to finish its entrance, so a rig parked after it still shows its pose. */
export const ENTRANCE_MS = 1500;
/** Backing-store pixels per CSS pixel for every cameo, on any display. */
const RIG_DPR = 2;

/**
 * One of the seven product characters in its fourteen-state rig, for the cameos that grip
 * and hang from the page. `park` pauses the rig and stops rendering, keeping the frame, that
 * many milliseconds after its state was applied; null keeps it live. The `.riv` is fetched
 * only once the cameo is within a screen of the viewport, so the hero loads first.
 */
export function Cameo(props: Readonly<{ colour: AgentColour; state: CharacterState; park: number | null; size: number; gazeX: number; gazeY: number; style?: CSSProperties; testId: string; onReady?: () => void; zoom?: number; eager?: boolean }>): JSX.Element {
  const resources = createAgentCharacterResources(`/agent/utrace-agent-${props.colour}.riv`, "/agent/rive.wasm");
  configureAgentCharacterRuntime(resources);
  const host = useRef<HTMLDivElement>(null);
  // live or parked, a rig is fetched only once its host is within a screen of the viewport, unless it is eager (the hero's own rigs)
  const near = useNearViewport(host, 1000) || props.eager === true;
  const riveFile = useLandingRiveFile(resources.assetUrl, near);
  const { rive, RiveComponent } = useRive(
    riveFile !== null ? { riveFile, artboard: "AgentCharacter", stateMachines: "AgentCharacterMachine", autoBind: true, autoplay: true, layout: new Layout({ fit: Fit.Contain, alignment: Alignment.Center }), enableRiveAssetCDN: false } : null,
    { useOffscreenRenderer: true, shouldResizeCanvasToContainer: true, customDevicePixelRatio: RIG_DPR },
  );
  const instance = rive?.viewModelInstance ?? null;
  const { setValue: setState } = useViewModelInstanceEnum("state", instance);
  const { setValue: setX } = useViewModelInstanceNumber("gazeX", instance);
  const { setValue: setY } = useViewModelInstanceNumber("gazeY", instance);
  const applied = useRef<{ state: CharacterState | null; x: number | null; y: number | null }>({ state: null, x: null, y: null });
  useEffect(() => {
    if (instance === null) {
      applied.current = { state: null, x: null, y: null };
      return;
    }
    if (applied.current.state !== props.state) {
      applied.current.state = props.state;
      setState(props.state);
    }
    if (applied.current.x !== props.gazeX) {
      applied.current.x = props.gazeX;
      setX(props.gazeX);
    }
    if (applied.current.y !== props.gazeY) {
      applied.current.y = props.gazeY;
      setY(props.gazeY);
    }
  }, [instance, setState, setX, setY, props.state, props.gazeX, props.gazeY]);
  const { onReady } = props;
  useEffect(() => {
    if (rive !== null) onReady?.();
  }, [rive, onReady]);
  // a zoomed container changes the drawn size without resizing the element, so a new zoom refits
  useEffect(() => {
    if (rive !== null) fitRigCanvas(rive, host.current, RIG_DPR);
  }, [rive, props.zoom]);
  // before paint: sizing a new canvas draws the rig's first frame, so a character never paints one empty frame first
  useLayoutEffect(() => {
    if (rive === null) return;
    fitRigCanvas(rive, host.current, RIG_DPR);
    rive.startRendering();
    rive.play("AgentCharacterMachine");
    if (props.park === null) return;
    const timer = window.setTimeout(() => {
      rive.pause("AgentCharacterMachine");
      rive.stopRendering();
    }, props.park);
    return () => window.clearTimeout(timer);
  }, [rive, props.park, props.state]);
  return (
    <div ref={host} data-testid={props.testId} data-state={props.state} style={{ position: "absolute", width: props.size, height: props.size, lineHeight: 0, ...props.style }}>
      <RiveComponent aria-hidden="true" style={{ width: "100%", height: "100%" }} />
    </div>
  );
}
