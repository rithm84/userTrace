import type { CSSProperties, JSX, ReactNode } from "react";

/** A comic sticker: a white disc with an ink edge and a hard shadow, the frame a cameo sits in. */
export function Badge(props: Readonly<{ size: number; on: boolean; square?: boolean; children: ReactNode; style?: CSSProperties }>): JSX.Element {
  return (
    <div className={"lp-badge" + (props.on ? " is-on" : "") + (props.square ? " is-square" : "")} style={{ width: props.size, height: props.size, ...props.style }}>
      {props.children}
    </div>
  );
}

/** A decorative pointer that lets clicks reach the control underneath. */
export function CursorArrow(props: Readonly<{ size?: number; style?: CSSProperties }>): JSX.Element {
  const size = props.size ?? 22;
  return (
    <svg width={size} height={size * 1.4} viewBox="0 0 20 28" aria-hidden="true" style={{ ...props.style, pointerEvents: "none" }}>
      <path d="M2 2 L2 22 L7.2 17.4 L10.4 25.6 L13.8 24.2 L10.6 16.2 L17.6 16.2 Z" fill="#000" stroke="#fff" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}
