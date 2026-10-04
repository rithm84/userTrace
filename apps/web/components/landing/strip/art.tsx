import type { CSSProperties, JSX, ReactNode } from "react";

/** A jagged impact burst: white inside a 2px ink edge, the frame-breaking device behind a bursting character. */
export function Burst(props: Readonly<{ size: number; style?: CSSProperties; className?: string }>): JSX.Element {
  const points: string[] = [];
  const spikes = 14;
  for (let i = 0; i < spikes * 2; i++) {
    const angle = (i / (spikes * 2)) * Math.PI * 2 - Math.PI / 2;
    const radius = i % 2 === 0 ? 50 : 30 + ((i * 7) % 5) * 1.6;
    points.push(`${(50 + Math.cos(angle) * radius).toFixed(2)},${(50 + Math.sin(angle) * radius).toFixed(2)}`);
  }
  return (
    <svg width={props.size} height={props.size} viewBox="-4 -4 108 108" aria-hidden="true" className={props.className} style={props.style}>
      <polygon points={points.join(" ")} fill="#fff" stroke="#000" strokeWidth="2.4" strokeLinejoin="miter" />
    </svg>
  );
}

/** Three short focus lines radiating from a corner. */
export function FocusLines(props: Readonly<{ style?: CSSProperties }>): JSX.Element {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" aria-hidden="true" style={props.style}>
      <g stroke="#000" strokeWidth="2.4" strokeLinecap="round">
        <line x1="6" y1="20" x2="16" y2="24" />
        <line x1="14" y1="6" x2="20" y2="16" />
        <line x1="28" y1="4" x2="27" y2="15" />
      </g>
    </svg>
  );
}

/** A speech balloon with an angled tail, lettered in the comic face; the one balloon on the page. */
export function Balloon(props: Readonly<{ text: string; width: number; style?: CSSProperties; testId?: string }>): JSX.Element {
  return (
    <div data-testid={props.testId} aria-hidden="true" style={{ position: "absolute", width: props.width, ...props.style }}>
      <svg width="100%" viewBox="0 0 160 84" aria-hidden="true" style={{ display: "block", overflow: "visible" }}>
        <path d="M14 4 H146 Q156 4 156 14 V50 Q156 60 146 60 H60 L34 80 L42 60 H14 Q4 60 4 50 V14 Q4 4 14 4 Z" fill="#fff" stroke="#000" strokeWidth="2.5" strokeLinejoin="round" />
        <text x="80" y="42" textAnchor="middle" fontFamily="var(--sfx)" fontSize="30" fill="#000" style={{ letterSpacing: "0.02em" }}>
          {props.text}
        </text>
      </svg>
    </div>
  );
}

/** A comic sticker: a white disc with an ink edge and a hard shadow, the frame a cameo sits in. */
export function Badge(props: Readonly<{ size: number; on: boolean; square?: boolean; children: ReactNode; style?: CSSProperties }>): JSX.Element {
  return (
    <div className={"strip-badge" + (props.on ? " is-on" : "") + (props.square ? " is-square" : "")} style={{ width: props.size, height: props.size, ...props.style }}>
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

/** A small wall calendar, typographic, with the demo date ringed in coral. October 2026 starts on a Thursday; the 3rd is a Saturday. */
export function WallCalendar(props: Readonly<{ style?: CSSProperties }>): JSX.Element {
  const days = ["M", "T", "W", "T", "F", "S", "S"];
  const leadingBlanks = 3;
  return (
    <div aria-hidden="true" data-testid="strip-calendar" style={{ width: 118, padding: "8px 8px 7px", background: "#fff", border: "2px solid #000", boxShadow: "3px 3px 0 #000", fontFamily: "var(--body)", ...props.style }}>
      <div style={{ font: "700 12px/1 var(--body)", letterSpacing: "-0.01em", marginBottom: 6 }}>October</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: "3px 0", font: "500 8.5px/1 var(--body)", color: "#777", textAlign: "center" }}>
        {days.map((day, index) => (
          <span key={index}>{day}</span>
        ))}
        {Array.from({ length: leadingBlanks }, (_, index) => (
          <span key={"blank-" + index} aria-hidden="true" />
        ))}
        {Array.from({ length: 31 }, (_, index) => index + 1).map((date) => (
          <span key={date} style={{ position: "relative", color: date === 3 ? "#000" : "#333", fontWeight: date === 3 ? 700 : 500, lineHeight: "12px" }}>
            {date === 3 ? <i style={{ position: "absolute", left: "50%", top: "50%", width: 14, height: 14, translate: "-50% -50%", border: "2px solid var(--coral-strong)", borderRadius: "50%" }} /> : null}
            {date}
          </span>
        ))}
      </div>
    </div>
  );
}
