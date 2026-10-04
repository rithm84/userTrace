import type { AgentColour } from '../characters/AgentSprite';
import type { Box, Pt } from './iso';
import type { Tint } from './paint';
import type { Cloud, Figure, Plant } from './sprites';

/** The product surfaces a building can wear as a facade. */
export type AppKind = 'chat' | 'dashboard' | 'gallery' | 'settings' | 'form' | 'profile';

export type Facade =
  | Readonly<{ kind: 'windows'; lit: readonly number[]; door: boolean }>
  | Readonly<{ kind: 'app'; app: AppKind }>
  | Readonly<{ kind: 'shop'; awning: Tint; app: AppKind | null }>
  | Readonly<{ kind: 'house' }>
  | Readonly<{ kind: 'blank' }>;

export type Roof = 'plain' | 'units' | 'tank';


/** What an agent is doing; the choreography turns it into the rig's state over time. */
export type AgentCue = 'investigate' | 'interview' | 'uncover' | 'build' | 'validate' | 'deliver';

type Placed = Readonly<{ id: string; box: Box }>;

export type Item =
  | (Placed & Readonly<{ kind: 'building'; tint: Tint; left: Facade; right: Facade; roof: Roof }>)
  | (Placed & Readonly<{ kind: 'tree'; plant: Plant; size: number; flip: boolean }>)
  | (Placed & Readonly<{ kind: 'lamp' }>)
  | (Placed & Readonly<{ kind: 'bench'; along: 'x' | 'y' }>)
  | (Placed & Readonly<{ kind: 'planter' }>)
  | (Placed & Readonly<{ kind: 'table' }>)
  | (Placed & Readonly<{ kind: 'dig' }>)
  | (Placed & Readonly<{ kind: 'frame' }>)
  | (Placed & Readonly<{ kind: 'crane'; jib: number }>)
  | (Placed & Readonly<{ kind: 'load' }>)
  | (Placed & Readonly<{ kind: 'person'; figure: Figure; flip: boolean; tempoMs: number; offsetMs: number; cued: boolean }>)
  | (Placed & Readonly<{ kind: 'agent'; colour: AgentColour; size: number; cue: AgentCue }>)
  | (Placed & Readonly<{ kind: 'trike'; route: readonly [number, number] }>);

/** Flat ground drawn beneath everything, in order. */
export type Ground =
  | Readonly<{ kind: 'street'; x: number; y: number; w: number; d: number; along: 'x' | 'y'; lanes: boolean }>
  | Readonly<{ kind: 'slab'; x: number; y: number; w: number; d: number }>
  | Readonly<{ kind: 'path'; x: number; y: number; w: number; d: number; along: 'x' | 'y'; sign: 1 | -1 }>
  | Readonly<{ kind: 'crossing'; x: number; y: number; w: number; d: number; along: 'x' | 'y' }>
  | Readonly<{ kind: 'lawn'; x: number; y: number; w: number; d: number }>
  | Readonly<{ kind: 'lot'; x: number; y: number; w: number; d: number }>
  | Readonly<{ kind: 'deck'; x: number; y: number; w: number; d: number }>
  | Readonly<{ kind: 'manhole'; x: number; y: number }>;

/** A speech or feedback bubble floating above the scene, in front of everything. */
export type Bubble = Readonly<{ id: string; at: readonly [number, number, number]; glyph: 'bug' | 'star' | 'question' | 'heart' | 'lines' | 'alert'; cue: 'feed' | 'customer' | 'pain' | 'reply' }>;

/** A cloud in the sky above the towers, in design pixels, drifting `drift` pixels to and fro. */
/** A cloud at `size` times its drawn scale, drifting `drift` px either way in front of the towers. */
export type Sky = Readonly<{ cloud: Cloud; at: Pt; size: number; drift: number }>;

export type CityPlan = Readonly<{ ground: readonly Ground[]; items: readonly Item[]; bubbles: readonly Bubble[]; sky: readonly Sky[] }>;
