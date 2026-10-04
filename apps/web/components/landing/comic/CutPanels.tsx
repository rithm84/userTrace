import type { CSSProperties, JSX, ReactNode } from "react";

export type PanelPoint = readonly [number, number];

export type CutPanel = Readonly<{
  id: string;
  /** Polygon in the group's coordinate box. */
  points: readonly PanelPoint[];
  children: ReactNode;
  background?: string;
}>;

function polygon(points: readonly PanelPoint[], width: number, height: number): string {
  return `polygon(${points.map(([x, y]) => `${((x / width) * 100).toFixed(3)}% ${((y / height) * 100).toFixed(3)}%`).join(", ")})`;
}

/**
 * Panels cut from one box by clip-path polygons
 * (so the content follows the diagonal gutters) and outlined by a sibling SVG
 * whose strokes do not scale, so the 2px ink survives any box size.
 */
export function CutPanels(props: Readonly<{
  width: number;
  height: number;
  panels: readonly CutPanel[];
  style?: CSSProperties;
  className?: string;
}>): JSX.Element {
  return (
    <div className={props.className} style={{ position: "relative", aspectRatio: `${props.width} / ${props.height}`, ...props.style }}>
      {props.panels.map((panel) => (
        <div
          key={panel.id}
          data-panel={panel.id}
          style={{
            position: "absolute",
            inset: 0,
            clipPath: polygon(panel.points, props.width, props.height),
            background: panel.background ?? "var(--paper)",
          }}
        >
          {panel.children}
        </div>
      ))}
      <svg
        aria-hidden="true"
        viewBox={`0 0 ${props.width} ${props.height}`}
        preserveAspectRatio="none"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none", overflow: "visible" }}
      >
        {props.panels.map((panel) => (
          <polygon
            key={panel.id}
            points={panel.points.map(([x, y]) => `${x},${y}`).join(" ")}
            fill="none"
            stroke="var(--ink)"
            strokeWidth={2}
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
    </div>
  );
}
