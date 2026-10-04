/** Five DOM tiles backed by one lazily created Matter world, parked when asleep or offscreen. */
"use client";

import { useEffect, useEffectEvent, useRef, type CSSProperties, type JSX } from "react";

import { useReducedMotion } from "@/components/agent/useReducedMotion";

import { PLATFORMS, type Platform } from "./platforms";

const TILE_SIZE = 72;
/** The physics body is a little larger than the drawn tile, so solver penetration never shows as one tile's edge crossing another's. */
const BODY_SIZE = TILE_SIZE + 8;
const DROP_INTERVAL_MS = 120;
const DRAG_THRESHOLD_PX = 6;
const DROP_LANES: readonly number[] = [0.2, 0.45, 0.7, 0.36, 0.59];

type MatterModule = typeof import("matter-js");
type Body = import("matter-js").Body;
type Engine = import("matter-js").Engine;
type Runner = import("matter-js").Runner;
type Constraint = import("matter-js").Constraint;
type PilePoint = Readonly<{ x: number; y: number }>;
type Pose = Readonly<{ x: number; y: number; angle: number }>;
type TileStyle = CSSProperties & Readonly<{ "--tile-index": number }>;
type PileProps = Readonly<{
  selectedId: string | null;
  onSelect: (platform: Platform) => void;
}>;
type TileBody = {
  readonly platform: Platform;
  readonly body: Body;
  readonly element: HTMLButtonElement;
  pose: Pose | null;
};
type Drag = {
  readonly pointerId: number;
  readonly tile: TileBody;
  readonly constraint: Constraint;
  readonly start: PilePoint;
  last: PilePoint;
  lastTime: number;
  velocity: PilePoint;
  moved: boolean;
};

/** Owns the external physics engine, native input, and DOM pose writes. */
class PileWorld {
  private readonly engine: Engine;
  private runner: Runner | null = null;
  private readonly tiles: TileBody[] = [];
  private pendingTile: TileBody | null = null;
  private nextDropAt = 0;
  private walls: Body[] = [];
  private width: number;
  private readonly height: number;
  private visible = false;
  private drag: Drag | null = null;
  private hovered: TileBody | null = null;
  private readonly resizeObserver: ResizeObserver;

  constructor(
    private readonly Matter: MatterModule,
    private readonly box: HTMLDivElement,
    private readonly elements: ReadonlyMap<string, HTMLButtonElement>,
    private readonly onSelect: (platform: Platform) => void,
  ) {
    this.width = box.clientWidth;
    this.height = box.clientHeight;
    this.engine = Matter.Engine.create({ enableSleeping: true, positionIterations: 10, velocityIterations: 8 });
    this.engine.gravity.y = 1.1;
    this.replaceWalls();
    box.dataset.mode = "physics";
    box.dataset.running = "false";
    box.dataset.dragging = "false";
    Matter.Events.on(this.engine, "beforeUpdate", this.dropNextTile);
    box.addEventListener("pointerdown", this.pointerDown);
    box.addEventListener("pointermove", this.pointerMove);
    box.addEventListener("pointerover", this.pointerOver);
    box.addEventListener("pointerleave", this.pointerLeave);
    box.addEventListener("pointerup", this.pointerUp);
    box.addEventListener("pointercancel", this.pointerCancel);
    box.addEventListener("lostpointercapture", this.pointerCancel);
    box.addEventListener("click", this.suppressPointerClick, true);
    document.addEventListener("visibilitychange", this.visibilityChanged);
    this.resizeObserver = new ResizeObserver(this.resize);
    this.resizeObserver.observe(box);
  }

  setVisible(visible: boolean): void {
    this.visible = visible;
    if (visible && !document.hidden) {
      this.start();
    } else {
      this.releaseDrag();
      this.hovered = null;
      this.stop();
    }
  }

  selectWithKeyboard(platform: Platform): void {
    const tile = this.tiles.find((entry) => entry.platform.id === platform.id);
    if (tile !== undefined) {
      this.Matter.Sleeping.set(tile.body, false);
      this.start();
    }
    this.onSelect(platform);
  }

  destroy(): void {
    this.visible = false;
    this.releaseDrag();
    this.stop();
    this.resizeObserver.disconnect();
    const box = this.box;
    box.removeEventListener("pointerdown", this.pointerDown);
    box.removeEventListener("pointermove", this.pointerMove);
    box.removeEventListener("pointerover", this.pointerOver);
    box.removeEventListener("pointerleave", this.pointerLeave);
    box.removeEventListener("pointerup", this.pointerUp);
    box.removeEventListener("pointercancel", this.pointerCancel);
    box.removeEventListener("lostpointercapture", this.pointerCancel);
    box.removeEventListener("click", this.suppressPointerClick, true);
    document.removeEventListener("visibilitychange", this.visibilityChanged);
    this.Matter.Events.off(this.engine, "beforeUpdate", this.dropNextTile);
    this.Matter.Composite.clear(this.engine.world, false, true);
    this.Matter.Engine.clear(this.engine);
    this.tiles.length = 0;
    this.pendingTile = null;
    this.walls = [];
    this.hovered = null;
    for (const element of this.elements.values()) {
      element.style.removeProperty("transform");
      delete element.dataset.spawned;
    }
    delete box.dataset.mode;
    delete box.dataset.running;
    delete box.dataset.dragging;
  }

  private start(): void {
    if (this.runner !== null || !this.visible || document.hidden) return;
    // A fresh Runner clock resumes the same Engine without accumulating offscreen time.
    this.runner = this.Matter.Runner.create({ delta: 1000 / 60 });
    this.Matter.Events.on(this.runner, "afterTick", this.afterFrame);
    this.box.dataset.running = "true";
    this.Matter.Runner.run(this.runner, this.engine);
  }

  private stop(): void {
    if (this.runner !== null) {
      this.Matter.Runner.stop(this.runner);
      this.Matter.Events.off(this.runner, "afterTick", this.afterFrame);
      this.runner = null;
    }
    this.box.dataset.running = "false";
  }

  private readonly visibilityChanged = (): void => {
    this.setVisible(this.visible);
  };

  private replaceWalls(): void {
    const { Bodies, Composite } = this.Matter;
    for (const wall of this.walls) Composite.remove(this.engine.world, wall);
    const options = { isStatic: true, restitution: 0.2, friction: 0.6 };
    this.walls = [
      Bodies.rectangle(this.width / 2, this.height + 44, this.width + 200, 100, options),
      Bodies.rectangle(-44, this.height / 2, 100, this.height + 400, options),
      Bodies.rectangle(this.width + 44, this.height / 2, 100, this.height + 400, options),
      Bodies.rectangle(this.width / 2, -160, this.width + 200, 100, options),
    ];
    Composite.add(this.engine.world, this.walls);
  }

  private readonly resize = (): void => {
    const width = this.box.clientWidth;
    if (width === this.width) return;
    const oldWidth = this.width;
    this.width = width;
    this.pendingTile = null;
    this.releaseDrag();
    this.replaceWalls();
    for (const { body } of this.tiles) {
      const margin = TILE_SIZE / Math.sqrt(2) + 6;
      this.Matter.Body.setPosition(body, {
        x: Math.max(margin, Math.min(width - margin, body.position.x * width / oldWidth)),
        y: body.position.y,
      });
      this.Matter.Sleeping.set(body, false);
    }
    this.start();
  };

  private readonly dropNextTile = (): void => {
    const index = this.tiles.length;
    if (index >= PLATFORMS.length || this.engine.timing.timestamp < this.nextDropAt) return;
    this.pendingTile ??= this.createTile(index);
    const tile = this.pendingTile;
    // Let the entrance clear: intersecting a falling body would eject it sideways.
    if (this.Matter.Query.collides(tile.body, this.tiles.map(({ body }) => body)).length > 0) return;
    this.tiles.push(tile);
    this.Matter.Composite.add(this.engine.world, tile.body);
    this.pendingTile = null;
    this.nextDropAt = this.engine.timing.timestamp + DROP_INTERVAL_MS;
  };

  private createTile(index: number): TileBody {
    const platform = PLATFORMS[index];
    const element = this.elements.get(platform.id);
    if (element === undefined) throw new Error(`Cannot drop ${platform.id}: its platform button is missing.`);
    const body = this.Matter.Bodies.rectangle(
      TILE_SIZE + DROP_LANES[index] * (this.width - TILE_SIZE * 2),
      -BODY_SIZE / 2 - 8,
      BODY_SIZE,
      BODY_SIZE,
      {
        label: platform.id,
        chamfer: { radius: 14 },
        slop: 0.01,
        restitution: 0.25,
        friction: 0.5,
        frictionAir: 0.015,
        density: 0.002,
        angle: ((index % 3) - 1) * 0.2,
      },
    );
    return { platform, element, body, pose: null };
  }

  /** Runner afterTick runs once per displayed frame, after all Engine substeps. */
  private readonly afterFrame = (): void => {
    for (const tile of this.tiles) {
      const { body, element, pose } = tile;
      const { x, y } = body.position;
      if (pose !== null && pose.x === x && pose.y === y && pose.angle === body.angle) continue;
      element.style.transform = `translate(${x - TILE_SIZE / 2}px, ${y - TILE_SIZE / 2}px) rotate(${body.angle}rad)`;
      element.dataset.spawned = "true";
      tile.pose = { x, y, angle: body.angle };
    }
    if (this.tiles.length === PLATFORMS.length && this.drag === null && this.tiles.every(({ body }) => body.isSleeping)) {
      this.stop();
    }
  };

  private point(event: PointerEvent): PilePoint {
    const rect = this.box.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * this.width / rect.width,
      y: (event.clientY - rect.top) * this.height / rect.height,
    };
  }

  private hit(point: PilePoint): TileBody | null {
    const hits = this.Matter.Query.point(this.tiles.map(({ body }) => body), point);
    // Later DOM siblings paint on top, so choose the last overlapping body.
    const body = hits[hits.length - 1];
    return this.tiles.find((tile) => tile.body === body) ?? null;
  }

  private nudge(tile: TileBody): void {
    this.Matter.Sleeping.set(tile.body, false);
    this.Matter.Body.setAngularVelocity(tile.body, tile.body.angularVelocity + (tile.body.position.x < this.width / 2 ? 0.025 : -0.025));
    this.start();
  }

  private readonly pointerOver = (event: PointerEvent): void => {
    if (!event.isPrimary || this.drag !== null || event.pointerType === "touch") return;
    const tile = this.hit(this.point(event));
    if (tile !== null && tile !== this.hovered) this.nudge(tile);
    this.hovered = tile;
  };

  private readonly pointerLeave = (): void => {
    this.hovered = null;
  };

  private readonly pointerDown = (event: PointerEvent): void => {
    if (!event.isPrimary || event.button !== 0 || this.drag !== null) return;
    const point = this.point(event);
    const tile = this.hit(point);
    if (tile === null) return;
    // a press never takes focus: focusing from script would show the keyboard ring after any key
    // pressed earlier on the page; keyboard users reach the tiles with Tab
    this.box.setPointerCapture(event.pointerId);
    this.Matter.Sleeping.set(tile.body, false);
    const constraint = this.Matter.Constraint.create({
      pointA: point,
      bodyB: tile.body,
      pointB: { x: point.x - tile.body.position.x, y: point.y - tile.body.position.y },
      length: 0,
      stiffness: 0.12,
      damping: 0.1,
      render: { visible: false },
    });
    this.Matter.Composite.add(this.engine.world, constraint);
    this.drag = {
      pointerId: event.pointerId,
      tile,
      constraint,
      start: { x: event.clientX, y: event.clientY },
      last: point,
      lastTime: event.timeStamp,
      velocity: { x: 0, y: 0 },
      moved: false,
    };
    this.start();
    event.preventDefault();
  };

  private readonly pointerMove = (event: PointerEvent): void => {
    const drag = this.drag;
    if (drag === null) {
      this.pointerOver(event);
      return;
    }
    if (drag.pointerId !== event.pointerId) return;
    const point = this.point(event);
    const elapsed = event.timeStamp - drag.lastTime;
    if (elapsed > 0) {
      const scale = (1000 / 60) / elapsed;
      const vx = (point.x - drag.last.x) * scale;
      const vy = (point.y - drag.last.y) * scale;
      const limit = Math.max(1, Math.hypot(vx, vy) / 18);
      drag.velocity = { x: vx / limit, y: vy / limit };
    }
    drag.last = point;
    drag.lastTime = event.timeStamp;
    drag.moved ||= Math.hypot(event.clientX - drag.start.x, event.clientY - drag.start.y) >= DRAG_THRESHOLD_PX;
    this.box.dataset.dragging = String(drag.moved);
    if (drag.moved) {
      drag.constraint.pointA = {
        x: Math.max(TILE_SIZE / 2, Math.min(this.width - TILE_SIZE / 2, point.x)),
        y: Math.max(TILE_SIZE / 2, Math.min(this.height - TILE_SIZE / 2, point.y)),
      };
    }
    this.Matter.Sleeping.set(drag.tile.body, false);
    this.start();
  };

  private readonly pointerUp = (event: PointerEvent): void => {
    const drag = this.drag;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    const moved = drag.moved || Math.hypot(event.clientX - drag.start.x, event.clientY - drag.start.y) >= DRAG_THRESHOLD_PX;
    this.releaseDrag();
    if (moved) {
      this.Matter.Body.setVelocity(drag.tile.body, event.timeStamp - drag.lastTime < 80 ? drag.velocity : { x: 0, y: 0 });
    } else {
      this.onSelect(drag.tile.platform);
    }
    this.Matter.Sleeping.set(drag.tile.body, false);
    this.start();
  };

  private readonly pointerCancel = (event: PointerEvent): void => {
    if (this.drag?.pointerId !== event.pointerId) return;
    this.releaseDrag();
  };

  private releaseDrag(): void {
    const drag = this.drag;
    if (drag === null) return;
    this.drag = null;
    this.Matter.Composite.remove(this.engine.world, drag.constraint);
    this.box.dataset.dragging = "false";
    if (this.box.hasPointerCapture(drag.pointerId)) this.box.releasePointerCapture(drag.pointerId);
  }

  private readonly suppressPointerClick = (event: MouseEvent): void => {
    // Pointer selection is decided on release; keyboard/assistive clicks retain native semantics.
    if (event.detail > 0) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
}

export function PlatformPile(props: PileProps): JSX.Element {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const tileRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const worldRef = useRef<PileWorld | null>(null);
  const reducedMotion = useReducedMotion();
  const select = useEffectEvent((platform: Platform): void => props.onSelect(platform));

  useEffect(() => {
    const box = boxRef.current;
    if (box === null || reducedMotion) return;
    let disposed = false;
    let loading = false;
    let visible = false;
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[0];
      if (entry === undefined) return;
      visible = entry.isIntersecting;
      if (worldRef.current !== null) {
        worldRef.current.setVisible(visible);
      } else if (visible && !loading) {
        loading = true;
        void import("matter-js").then((Matter) => {
          if (disposed) return;
          const world = new PileWorld(Matter, box, tileRefs.current, select);
          worldRef.current = world;
          world.setVisible(visible);
        });
      }
    }, { rootMargin: "200px", threshold: 0 });
    observer.observe(box);
    return () => {
      disposed = true;
      observer.disconnect();
      worldRef.current?.destroy();
      worldRef.current = null;
    };
  }, [reducedMotion]);

  return (
    <div ref={boxRef} className="fits-pile" data-testid="platform-pile" data-reduced-motion={reducedMotion} role="group" aria-label="Explore Customer Surfaces">
      {PLATFORMS.map((platform, index) => (
        <button
          key={platform.id}
          ref={(element) => {
            if (element === null) tileRefs.current.delete(platform.id);
            else tileRefs.current.set(platform.id, element);
          }}
          className="fits-tile"
          style={{ "--tile-index": index } as TileStyle}
          type="button"
          data-testid={`platform-tile-${platform.id}`}
          aria-label={`Explore ${platform.name}`}
          aria-pressed={props.selectedId === platform.id}
          aria-controls="fits-caption"
          onClick={() => {
            const world = worldRef.current;
            if (world !== null) {
              world.selectWithKeyboard(platform);
            } else {
              props.onSelect(platform);
            }
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- existing static brand marks */}
          <img alt="" src={platform.logo} width={40} height={40} draggable={false} />
        </button>
      ))}
    </div>
  );
}
