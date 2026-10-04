/**
 * The packaged interface styles for the closed shadow root.
 *
 * They follow `docs/DESIGN.md`: its ink, paper, and coral tokens, ink borders,
 * one coral accent, hard offset shadows rather than blurs, a round sticker for
 * the character, and bold sentence-case labels. The orb is product UI, so it
 * moves with short eased transitions and never scales: a scaled box would
 * resample the character's canvas and the labels. No remote stylesheet or font
 * is loaded, and nothing here alters the product's own CSS or stacking
 * contexts.
 *
 * The tokens live on the layers inside the shadow root rather than on `:host`,
 * because a product rule that reaches the host element would outrank them.
 */
import { ORB_LAYOUT, px } from "./orbGeometry";

export const SURFACE_STYLES = `
:host { all: initial; }
* { box-sizing: border-box; margin: 0; padding: 0; }

.layer {
  --ink: #000000;
  --ink-2: #3d3d3d;
  --paper: #ffffff;
  --coral-strong: #fa8d82;
  --coral-ink: #a63a2e;
  --coral-tint: #ffd3cc;
  --ease-out: cubic-bezier(0, 0, 0.2, 1);
  --ease-smooth: cubic-bezier(0.22, 1, 0.36, 1);
  --type: system-ui, -apple-system, "Segoe UI", sans-serif;
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.scene-layer { overflow: visible; }
.scene-mark { fill: none; stroke: var(--ink); stroke-width: 3; stroke-linecap: round; }
.scene-mark.coral { stroke: var(--coral-strong); }
.scene-label {
  position: fixed;
  background: var(--paper);
  border: 2px solid var(--ink);
  box-shadow: 3px 3px 0 0 var(--ink);
  color: var(--ink);
  font: 700 13px/1.25 var(--type);
  padding: 6px 10px;
  pointer-events: none;
}

.agent-cursor {
  position: fixed;
  left: 0;
  top: 0;
  width: 26px;
  height: 26px;
  pointer-events: none;
  opacity: 0;
  transform: translate3d(-100px, -100px, 0);
  transition: opacity 120ms linear;
  will-change: transform;
}
.agent-cursor[data-visible="true"] { opacity: 1; }

/* The root sits on the orb's whole-pixel centre; everything inside is offset
   from it by whole pixels. */
.orb-root {
  --tuck: 20px;
  --hover-reach: calc(var(--radius) + ${px(ORB_LAYOUT.pillGap)} + 10px);
  position: fixed;
  left: 0;
  top: 0;
  width: 0;
  height: 0;
  pointer-events: none;
  color: var(--ink);
  font: 700 14px/1 var(--type);
}

/* While the controls are open, a ring just wider than the orb keeps the
   pointer inside them as it crosses the gap to a pill. */
.orb-root[data-expanded="true"]::before {
  content: "";
  position: absolute;
  left: calc(-1 * var(--hover-reach));
  top: calc(-1 * var(--hover-reach));
  width: calc(2 * var(--hover-reach));
  height: calc(2 * var(--hover-reach));
  border-radius: 50%;
  pointer-events: auto;
}

.orb {
  position: absolute;
  left: calc(-1 * var(--radius));
  top: calc(-1 * var(--radius));
  width: var(--diameter);
  height: var(--diameter);
  border: 3px solid var(--ink);
  border-radius: 50%;
  background: var(--paper);
  box-shadow: ${px(ORB_LAYOUT.shadow)} ${px(ORB_LAYOUT.shadow)} 0 0 var(--ink);
  overflow: hidden;
  pointer-events: auto;
  cursor: grab;
  touch-action: none;
  user-select: none;
}
.orb[data-dragging="true"] { cursor: grabbing; }
.orb canvas { display: block; width: 100%; height: 100%; }

/* Each pill rests just behind the orb and travels out to its side. */
.pill {
  position: absolute;
  top: ${px(-ORB_LAYOUT.pillHeight / 2)};
  height: ${px(ORB_LAYOUT.pillHeight)};
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 14px;
  border: 2px solid var(--ink);
  border-radius: 999px;
  background: var(--paper);
  box-shadow: ${px(ORB_LAYOUT.shadow)} ${px(ORB_LAYOUT.shadow)} 0 0 var(--ink);
  color: var(--ink);
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
  appearance: none;
  opacity: 0;
  pointer-events: none;
  transition:
    transform 220ms var(--ease-smooth),
    opacity 160ms var(--ease-out),
    box-shadow 120ms var(--ease-out);
}
.pill svg { display: block; flex: none; }
.pill-top, .pill-bottom {
  left: ${px(-ORB_LAYOUT.stackPillWidth / 2)};
  width: ${px(ORB_LAYOUT.stackPillWidth)};
}
.pill-left, .pill-right {
  left: ${px(-ORB_LAYOUT.sidePillWidth / 2)};
  width: ${px(ORB_LAYOUT.sidePillWidth)};
}
.pill-top { transform: translate(0, calc(var(--tuck) - var(--stack-offset))); }
.pill-bottom { transform: translate(0, calc(var(--stack-offset) - var(--tuck))); }
.pill-left { transform: translate(calc(var(--tuck) - var(--side-offset)), 0); }
.pill-right { transform: translate(calc(var(--side-offset) - var(--tuck)), 0); }

.orb-root[data-expanded="true"] .pill { opacity: 1; pointer-events: auto; }
.orb-root[data-expanded="true"] .pill:disabled { opacity: 0.45; }
.orb-root[data-expanded="true"] .pill-top { transform: translate(0, calc(-1 * var(--stack-offset))); }
.orb-root[data-expanded="true"] .pill-bottom { transform: translate(0, var(--stack-offset)); }
.orb-root[data-expanded="true"] .pill-left { transform: translate(calc(-1 * var(--side-offset)), 0); }
.orb-root[data-expanded="true"] .pill-right { transform: translate(var(--side-offset), 0); }

/* Muted and paused read as held-down toggles; End, the destructive action,
   carries the strongest coral. */
.pill[data-state="muted"], .pill[data-state="paused"] { background: var(--coral-tint); }
.pill[data-tone="end"] { background: var(--coral-strong); }

/* Hover and press only shorten the shadow. Moving the button under the
   pointer would flip its hover state at the edge, and a press that slid the
   button out from under the pointer would lose the click. */
.pill:hover:not(:disabled), .confirm-actions button:hover:not(:disabled) {
  box-shadow: 1px 1px 0 0 var(--ink);
}
.pill:active:not(:disabled), .confirm-actions button:active:not(:disabled) {
  box-shadow: 0 0 0 0 var(--ink);
}
.pill:focus-visible, .confirm-actions button:focus-visible {
  outline: 3px solid var(--coral-ink);
  outline-offset: 3px;
}
.pill:disabled, .confirm-actions button:disabled { cursor: not-allowed; }

/* End asks once more, where the End pill was. */
.confirm {
  position: absolute;
  width: ${px(ORB_LAYOUT.confirmationWidth)};
  display: none;
  padding: 14px;
  background: var(--paper);
  border: 3px solid var(--ink);
  box-shadow: 4px 4px 0 0 var(--ink);
  pointer-events: auto;
  transition: opacity 140ms var(--ease-out);
}
.confirm[data-open="true"] { display: block; }
@starting-style { .confirm[data-open="true"] { opacity: 0; } }
.confirm-question { font: 700 15px/1.3 var(--type); }
.confirm-detail { margin-top: 4px; font: 400 13px/1.4 var(--type); color: var(--ink-2); }
.confirm-actions { display: flex; gap: 8px; margin-top: 12px; }
.confirm-actions button {
  flex: 1;
  height: 36px;
  padding: 0 8px;
  border: 2px solid var(--ink);
  border-radius: 999px;
  background: var(--paper);
  box-shadow: ${px(ORB_LAYOUT.shadow)} ${px(ORB_LAYOUT.shadow)} 0 0 var(--ink);
  color: var(--ink);
  font: 700 14px/1 var(--type);
  white-space: nowrap;
  cursor: pointer;
  appearance: none;
  transition: box-shadow 120ms var(--ease-out);
}
.confirm-actions button[data-tone="end"] { background: var(--coral-strong); }
.confirm-actions button:disabled { opacity: 0.45; }

/* Reduced motion shows the finished state: the pills fade in place. */
@media (prefers-reduced-motion: reduce) {
  .orb-root { --tuck: 0px; }
  .pill, .confirm, .confirm-actions button { transition: opacity 120ms linear; }
}
`;
