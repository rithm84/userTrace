import type { CSSProperties, JSX } from "react";

import { AGENT_HEX, type AgentColour } from "../characters/AgentSprite";

type Look = "burst" | "star" | "cloud" | "crash" | "window" | "flash";

/** How the letters are drawn, alternating down the list: white SFX with an ink outline, or a colour fill inside a white inner and ink outer outline. */
type Lettering = "white" | "colour";

type RotatingEntry = Readonly<{
  word: string;
  colour: Exclude<AgentColour, "black">;
  look: Look;
  lettering: Lettering;
  /** Resting tilt in degrees. */
  rot: number;
  /** Width of the whole word box in em of the word's font size, measured live. */
  em: number;
}>;

/**
 * The rotating word in the headline: six things a Product Signal becomes,
 * each in one character's colour and one sound-effect device from the comic
 * page. The devices are built the way SFX art is: a main balloon, often a
 * second shape or a halftone field peeking out behind it, rays, stars or
 * puffs around it, and lettering with its own outline system. Every balloon
 * keeps a text margin so letters never meet the edge. Each word rests for
 * 2.5 s.
 */
export const ROTATING_WORDS: readonly RotatingEntry[] = [
  { word: "features", colour: "coral", look: "star", lettering: "colour", rot: 0, em: 5.99 },
  { word: "fixes", colour: "mint", look: "burst", lettering: "white", rot: -3, em: 3.67 },
  { word: "workflows", colour: "sky", look: "cloud", lettering: "colour", rot: -1.5, em: 6.53 },
  { word: "integrations", colour: "lilac", look: "crash", lettering: "white", rot: 1.5, em: 6.52 },
  { word: "dashboards", colour: "peach", look: "window", lettering: "colour", rot: -2, em: 5.83 },
  { word: "shortcuts", colour: "aqua", look: "flash", lettering: "white", rot: 2, em: 5.88 },
];

/** The darker sibling of each pastel, for second shapes, dot screens and coloured lettering. */
const DARK: Readonly<Record<Exclude<AgentColour, "black">, string>> = Object.freeze({
  coral: "#B65047",
  mint: "#268A48",
  sky: "#007EB7",
  lilac: "#815FB7",
  peach: "#AF5B09",
  aqua: "#008C8D",
});

const PERIOD_S = 2.5;
const TOTAL_S = PERIOD_S * ROTATING_WORDS.length;

/** Spiky shout balloon: spikes along the top and bottom, notches at the sides. */
const BURST =
  "4% 26%, 12% 6%, 21% 20%, 31% 2%, 42% 18%, 53% 0%, 64% 18%, 75% 2%, 86% 16%, 95% 6%, 100% 32%, 93% 50%, 100% 70%, 95% 94%, 85% 82%, 74% 100%, 62% 84%, 51% 98%, 40% 82%, 28% 100%, 17% 84%, 7% 96%, 0% 72%, 7% 50%, 0% 30%";

/** Deterministic PRNG so the hand-drawn jitter is the same on every render. */
function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function polar(points: readonly (readonly [number, number])[]): string {
  return points.map(([r, theta]) => `${(50 + r * Math.cos(theta)).toFixed(2)}% ${(50 + r * Math.sin(theta)).toFixed(2)}%`).join(", ");
}

/** A many-point starburst ("WOW!"): alternating outer and inner radii. */
function starPolygon(points: number, inner: number): string {
  const out: (readonly [number, number])[] = [];
  for (let i = 0; i < points * 2; i += 1) {
    out.push([i % 2 === 0 ? 50 : inner, -Math.PI / 2 + (i * Math.PI) / points]);
  }
  return polar(out);
}

/** A puffy cloud: bulges outward with inward cusps between them. */
function cloudPolygon(lobes: number, samples: number): string {
  const out: (readonly [number, number])[] = [];
  for (let i = 0; i < samples; i += 1) {
    const theta = (i / samples) * Math.PI * 2;
    out.push([40 + 10 * Math.abs(Math.sin((lobes * theta) / 2)), theta]);
  }
  return polar(out);
}

/**
 * A fan of thin rays ("BANG!"): one polygon whose centre is hidden behind the
 * balloon, so only the thin wedge tips show around it.
 */
function raysPolygon(count: number, seed: number): string {
  const rnd = seeded(seed);
  const out: (readonly [number, number])[] = [];
  const step = (Math.PI * 2) / count;
  for (let i = 0; i < count; i += 1) {
    const theta = i * step + rnd() * step * 0.4;
    const tip = 42 + rnd() * 8;
    const half = 0.012 + rnd() * 0.01;
    out.push([30, theta - half], [tip, theta], [30, theta + half]);
  }
  return polar(out);
}

const STAR = starPolygon(16, 40);
const CLOUD = cloudPolygon(9, 144);
/**
 * "WHAM!": a hand-drawn burst with fourteen bold spikes around a clear
 * rectangular body (9-91% by 21-79% of the box), so the fill wraps the word
 * and the spikes stay bold at any width.
 */
const WHAM =
  "9% 22%, 4% 4%, 18% 22%, 28% 1%, 38% 21%, 50% 0%, 60% 21%, 72% 2%, 80% 22%, 97% 6%, 91% 26%, 100% 44%, 90% 50%, 100% 62%, 91% 76%, 96% 96%, 79% 79%, 70% 100%, 60% 80%, 49% 99%, 38% 80%, 27% 100%, 18% 79%, 3% 95%, 9% 72%, 0% 58%, 10% 50%, 0% 38%";
const RAYS = raysPolygon(34, 5);

function keyframes(): string {
  const n = ROTATING_WORDS.length;
  const shows: string[] = [];
  const widths: string[] = [];
  ROTATING_WORDS.forEach((entry, index) => {
    const a = (index / n) * 100;
    const b = ((index + 1) / n) * 100;
    const rest = `rotate(${entry.rot}deg)`;
    shows.push(
      `.lp-rw .w${index}{opacity:${index === 0 ? 1 : 0};transform:${rest};animation:lp-rw-show${index} ${TOTAL_S}s infinite}` +
        `@keyframes lp-rw-show${index}{0%,${a.toFixed(2)}%{opacity:0;transform:scale(.55) rotate(${entry.rot - 9}deg)}` +
        `${(a + 0.04).toFixed(2)}%{opacity:1;transform:scale(1.07) rotate(${entry.rot + 1.5}deg)}` +
        `${(a + 1.1).toFixed(2)}%,${(b - 0.04).toFixed(2)}%{opacity:1;transform:scale(1) ${rest}}` +
        `${b.toFixed(2)}%,100%{opacity:0;transform:scale(1.12) ${rest}}}`,
    );
    widths.push(`${(a + 0.04).toFixed(2)}%,${(b - 0.04).toFixed(2)}%{width:${entry.em}em}`);
  });
  return `${shows.join("")}@keyframes lp-rw-width{${widths.join("")}}`;
}

const CSS = `
.lp-rw{display:inline-grid;align-items:center;justify-items:start;vertical-align:baseline;position:relative;line-height:1.05;margin:0 .13em;
  width:${ROTATING_WORDS[0].em}em;font-family:var(--sfx);font-weight:400;letter-spacing:.03em;color:var(--ink);overflow:visible;
  animation:lp-rw-width ${TOTAL_S}s cubic-bezier(.22,1,.36,1) infinite}
.lp-rw .words{grid-area:1/1;display:grid;grid-template-columns:minmax(0,100%);align-items:center;justify-items:center;width:100%;height:100%;overflow:visible}
.lp-rw .w{grid-area:1/1;position:relative;display:inline-block;white-space:nowrap;text-align:center;transform-origin:50% 50%;will-change:transform,opacity}
.lp-rw .back,.lp-rw .under,.lp-rw .shape,.lp-rw .deco,.lp-rw .over{position:absolute;pointer-events:none}
.lp-rw .back{z-index:1}
.lp-rw .under{inset:0;z-index:2}
.lp-rw .shape{inset:0;z-index:3}
.lp-rw .over{inset:0;z-index:4}
.lp-rw .deco{z-index:5}
.lp-rw .shape::before,.lp-rw .shape::after{content:"";position:absolute;inset:-.1em -.09em}
.lp-rw .shape::before{background:var(--ink)}
.lp-rw .shape::after{background:var(--c)}
/* lettering: a grid of stacked layers so a word can carry a coloured fill, a white inner outline, an ink outer outline and a shadow.
   The ink outline is ink text dilated by an SVG morphology filter rather than a text stroke: strokes miter, and at Bangers' sharp corners a wide miter shoots out as a wedge. */
.lp-rw .txt{position:relative;z-index:6;display:inline-grid;line-height:1.05}
.lp-rw .txt .l{grid-area:1/1;white-space:nowrap}
.lp-rw .ch{display:inline-block;transform-origin:50% 50%}
.lp-rw .let-white .l-out{color:var(--ink);filter:url(#lp-rw-ink-white);transform:translate(.06em,.06em)}
.lp-rw .let-white .l-fill{color:#fff;-webkit-text-stroke:.055em var(--ink);paint-order:stroke fill}
.lp-rw .let-colour .l-sh{color:var(--ink);filter:url(#lp-rw-ink-colour);transform:translate(.07em,.08em)}
.lp-rw .let-colour .l-out{color:var(--ink);filter:url(#lp-rw-ink-colour)}
.lp-rw .let-colour .l-fill{color:var(--d);-webkit-text-stroke:.065em #fff;paint-order:stroke fill}
/* --- burst (fixes): shout balloon on a hard offset shadow, impact ticks, bounced white letters */
.lp-rw .look-burst{padding:.49em .87em .41em}
.lp-rw .look-burst .under{inset:-.1em -.09em;background:var(--ink);clip-path:polygon(${BURST});transform:translate(.09em,.1em)}
.lp-rw .look-burst .shape::before,.lp-rw .look-burst .shape::after{clip-path:polygon(${BURST})}
.lp-rw .look-burst .shape::after{transform:scale(.94,.86)}
.lp-rw .look-burst .over{inset:-.1em -.09em;clip-path:polygon(${BURST});transform:scale(.94,.86);background:radial-gradient(var(--d) .05em,transparent .06em) 0 0/.2em .2em;
  -webkit-mask-image:linear-gradient(90deg,#000 6%,transparent 44%);mask-image:linear-gradient(90deg,#000 6%,transparent 44%)}
/* --- starburst (features): WOW!, a second ink star cast behind, sparkles, coloured letters */
.lp-rw .look-star{padding:.56em 1.32em .48em}
.lp-rw .look-star .under{inset:-.08em -.1em;background:var(--ink);clip-path:polygon(${STAR});transform:translate(.05em,.04em) rotate(2deg)}
.lp-rw .look-star .shape::before,.lp-rw .look-star .shape::after{inset:-.08em -.1em;clip-path:polygon(${STAR})}
.lp-rw .look-star .shape::after{transform:scale(.91,.88)}
.lp-rw .look-star .over{inset:-.08em -.1em;clip-path:polygon(${STAR});transform:scale(.91,.88);background:radial-gradient(var(--d) .05em,transparent .06em) 0 0/.2em .2em;
  -webkit-mask-image:linear-gradient(35deg,#000 8%,transparent 46%);mask-image:linear-gradient(35deg,#000 8%,transparent 46%)}
/* --- cloud with rays (workflows): BANG!, a fan of ink rays behind a puffy cloud, dots fading from the right, puffs at two corners, coloured letters */
.lp-rw .look-cloud{padding:.5em 1.05em .4em}
.lp-rw .look-cloud .back{inset:-.5em -.7em;background:var(--ink);clip-path:polygon(${RAYS})}
.lp-rw .look-cloud .shape::before,.lp-rw .look-cloud .shape::after{inset:-.12em -.08em;clip-path:polygon(${CLOUD})}
.lp-rw .look-cloud .shape::after{transform:scale(.93,.89)}
.lp-rw .look-cloud .over{inset:-.12em -.08em;clip-path:polygon(${CLOUD});transform:scale(.93,.89);background:radial-gradient(var(--d) .05em,transparent .06em) 0 0/.2em .2em;
  -webkit-mask-image:linear-gradient(270deg,#000 6%,transparent 44%);mask-image:linear-gradient(270deg,#000 6%,transparent 44%)}
/* --- wham (integrations): WHAM!, a bold hand-drawn burst with a flipped darker twin interlocking behind it, dots inside fading from the left, five-point stars at the corners, white letters */
.lp-rw .look-crash{padding:.5em .74em .36em}
.lp-rw .look-crash .back{inset:-.12em -.11em;background:var(--ink);clip-path:polygon(${WHAM});transform:scale(-1,-1) translate(-.07em,-.05em)}
.lp-rw .look-crash .back::after{content:"";position:absolute;inset:0;background:var(--d);clip-path:polygon(${WHAM});transform:scale(.95,.92)}
.lp-rw .look-crash .shape::before,.lp-rw .look-crash .shape::after{inset:-.12em -.1em;clip-path:polygon(${WHAM})}
.lp-rw .look-crash .shape::after{transform:scale(.94,.9)}
.lp-rw .look-crash .over{inset:-.12em -.1em;clip-path:polygon(${WHAM});transform:scale(.94,.9);background:radial-gradient(var(--d) .05em,transparent .06em) 0 0/.2em .2em;
  -webkit-mask-image:linear-gradient(100deg,#000 12%,transparent 50%);mask-image:linear-gradient(100deg,#000 12%,transparent 50%)}
/* --- window (dashboards): a comic browser window: dark title bar with three lights, peach body with a dot fade, ink bezel, offset shadow, coloured letters */
.lp-rw .look-window{padding:.48em .54em .26em}
.lp-rw .look-window .under{inset:-.1em -.1em;background:var(--ink);border-radius:.24em;transform:translate(.12em,.12em)}
.lp-rw .look-window .shape::before{inset:-.1em -.1em;border-radius:.24em}
.lp-rw .look-window .shape::after{inset:0;border-radius:.14em}
.lp-rw .look-window .over{inset:.24em 0 0 0;border-radius:0 0 .14em .14em;background:radial-gradient(var(--d) .05em,transparent .06em) 0 0/.2em .2em;
  -webkit-mask-image:linear-gradient(320deg,#000 8%,transparent 48%);mask-image:linear-gradient(320deg,#000 8%,transparent 48%)}
.lp-rw .bar{position:absolute;z-index:4;left:0;right:0;top:0;height:.24em;background:var(--d);border-radius:.14em .14em 0 0;border-bottom:.05em solid var(--ink)}
.lp-rw .bar::before{content:"";position:absolute;left:.13em;top:.07em;width:.1em;height:.1em;border-radius:50%;background:#fff;box-shadow:.18em 0 0 #fff,.36em 0 0 #fff}
/* --- flash (shortcuts): a slim lightning ribbon notched like a bolt at both ends, an ink echo trailing behind it, dots fading from the tail, a bolt at the head, white letters on a bounced baseline */
.lp-rw .look-flash{padding:.3em .96em .18em .86em}
.lp-rw .look-flash .shape::before,.lp-rw .look-flash .shape::after{inset:-.09em -.1em;clip-path:polygon(7% 0,100% 0,93% 40%,100% 40%,90% 100%,0 100%,7% 60%,0 60%)}
.lp-rw .look-flash .shape::after{transform:scale(.955,.88)}
.lp-rw .look-flash .back{inset:-.09em -.1em;background:var(--ink);clip-path:polygon(7% 0,100% 0,93% 40%,100% 40%,90% 100%,0 100%,7% 60%,0 60%);transform:translate(-.08em,.06em)}
.lp-rw .look-flash .over{inset:-.09em -.1em;clip-path:polygon(7% 0,100% 0,93% 40%,100% 40%,90% 100%,0 100%,7% 60%,0 60%);transform:scale(.955,.88);background:radial-gradient(var(--d) .05em,transparent .06em) 0 0/.2em .2em;
  -webkit-mask-image:linear-gradient(90deg,#000 6%,transparent 42%);mask-image:linear-gradient(90deg,#000 6%,transparent 42%)}
.lp-rw .bolt{position:absolute;z-index:5;right:-.2em;top:-.44em;width:.5em;height:.8em}
${keyframes()}
@media (prefers-reduced-motion: reduce){.lp-rw{animation:none;width:${ROTATING_WORDS[0].em}em}.lp-rw .w{animation:none!important;opacity:0}.lp-rw .w0{opacity:1!important}}
`;

type LetterMotion = "none" | "bounce" | "grow";

const MOTION: Readonly<Record<Look, LetterMotion>> = Object.freeze({
  burst: "bounce",
  star: "bounce",
  cloud: "bounce",
  crash: "bounce",
  window: "bounce",
  flash: "bounce",
});

function letterStyle(motion: LetterMotion, index: number, count: number): CSSProperties | undefined {
  if (motion === "bounce") {
    return { transform: `rotate(${index % 2 === 0 ? -2 : 2}deg) translateY(${index % 2 === 0 ? -0.035 : 0.035}em)` };
  }
  if (motion === "grow") {
    const scale = 0.92 + (0.16 * index) / Math.max(1, count - 1);
    return { transform: `scale(${scale.toFixed(3)})`, transformOrigin: "50% 100%" };
  }
  return undefined;
}

/** The word as stacked layers, every layer carrying identical per-letter transforms so the outlines register. */
function SfxText(props: Readonly<{ word: string; lettering: Lettering; motion: LetterMotion }>): JSX.Element {
  const layers = props.lettering === "colour" ? ["l-sh", "l-out", "l-fill"] : ["l-out", "l-fill"];
  const chars = Array.from(props.word);
  return (
    <span className={`txt let-${props.lettering}`}>
      {layers.map((layer) => (
        <span className={`l ${layer}`} key={layer} aria-hidden="true">
          {chars.map((char, index) => (
            <span className="ch" key={index} style={letterStyle(props.motion, index, chars.length)}>
              {char}
            </span>
          ))}
        </span>
      ))}
    </span>
  );
}

function Sparkle(props: Readonly<{ style: CSSProperties }>): JSX.Element {
  return (
    <svg className="deco" viewBox="0 0 24 24" style={{ width: ".34em", height: ".34em", ...props.style }} aria-hidden="true">
      <path d="M12 1.5 14.6 9.4 22.5 12 14.6 14.6 12 22.5 9.4 14.6 1.5 12 9.4 9.4z" fill="var(--c)" stroke="var(--ink)" strokeWidth="2.2" strokeLinejoin="round" />
    </svg>
  );
}

function Star5(props: Readonly<{ style: CSSProperties; fill?: "white" | "dark" | "colour" }>): JSX.Element {
  const fill = props.fill === "dark" ? "var(--d)" : props.fill === "colour" ? "var(--c)" : "#fff";
  return (
    <svg className="deco" viewBox="0 0 24 24" style={{ width: ".3em", height: ".3em", ...props.style }} aria-hidden="true">
      <path d="M12 1.8 15 9l7.6.4-6 4.8 2.1 7.4L12 17.4 5.3 21.6l2.1-7.4-6-4.8L9 9z" fill={fill} stroke="var(--ink)" strokeWidth="2.2" strokeLinejoin="round" />
    </svg>
  );
}

function Ticks(): JSX.Element {
  return (
    <svg className="deco" viewBox="0 0 40 40" style={{ width: ".42em", height: ".42em", right: "-.3em", top: "-.34em" }} aria-hidden="true">
      <path d="M6 30 16 14M20 36 26 18M4 14 14 6" fill="none" stroke="var(--ink)" strokeWidth="4.5" strokeLinecap="round" />
    </svg>
  );
}

/** A cluster of three cloud puffs, white with an ink outline, the inner overlaps covered. */
function Puffs(props: Readonly<{ style: CSSProperties }>): JSX.Element {
  const circles = [
    [14, 24, 11],
    [30, 16, 13],
    [44, 27, 10],
  ] as const;
  return (
    <svg className="deco" viewBox="0 0 58 44" style={{ width: ".7em", height: ".53em", ...props.style }} aria-hidden="true">
      {circles.map(([cx, cy, r]) => (
        <circle cx={cx} cy={cy} r={r} fill="#fff" stroke="var(--ink)" strokeWidth="3.4" key={`o${cx}`} />
      ))}
      {circles.map(([cx, cy, r]) => (
        <circle cx={cx} cy={cy} r={r - 1} fill="#fff" key={`i${cx}`} />
      ))}
    </svg>
  );
}

/** A lightning bolt at the ribbon's head. */
function Bolt(): JSX.Element {
  return (
    <svg className="bolt" viewBox="0 0 30 48" style={{ transform: "rotate(14deg)" }} aria-hidden="true">
      <path d="M19 1 3 26h10L9 47 27 20H16z" fill="var(--d)" stroke="var(--ink)" strokeWidth="3.2" strokeLinejoin="round" />
    </svg>
  );
}

function Decorations(props: Readonly<{ look: Look }>): JSX.Element | null {
  switch (props.look) {
    case "burst":
      return <Ticks />;
    case "star":
      return (
        <>
          <Sparkle style={{ left: "-.04em", top: "-.12em" }} />
          <Sparkle style={{ right: "-.1em", top: ".1em", width: ".26em", height: ".26em" }} />
          <Sparkle style={{ left: ".26em", bottom: "-.14em", width: ".2em", height: ".2em" }} />
        </>
      );
    case "cloud":
      return (
        <>
          <Puffs style={{ left: "-.26em", top: "-.16em" }} />
          <Puffs style={{ right: "-.22em", bottom: "-.18em", width: ".54em", height: ".41em" }} />
        </>
      );
    case "crash":
      return (
        <>
          <Star5 style={{ left: "-.24em", top: "-.22em" }} />
          <Star5 style={{ left: ".08em", bottom: "-.32em", width: ".22em", height: ".22em" }} fill="dark" />
          <Star5 style={{ right: "-.26em", bottom: "-.16em", width: ".26em", height: ".26em" }} />
          <Star5 style={{ right: ".12em", top: "-.32em", width: ".2em", height: ".2em" }} fill="dark" />
        </>
      );
    case "window":
      return (
        <>
          <span className="bar" />
          <Star5 style={{ right: "-.34em", top: "-.3em", width: ".3em", height: ".3em" }} fill="colour" />
          <Star5 style={{ left: "-.3em", bottom: "-.26em", width: ".22em", height: ".22em" }} fill="colour" />
        </>
      );
    case "flash":
      return <Bolt />;
    default:
      return null;
  }
}

/** The ink outlines as dilation filters; the radii are the outline half-widths in em of the word's font size, so they scale with it. */
function InkFilters(props: Readonly<{ fontSize: number }>): JSX.Element {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <filter id="lp-rw-ink-white" x="-20%" y="-20%" width="140%" height="140%">
          <feMorphology operator="dilate" radius={(props.fontSize * 0.07).toFixed(2)} />
        </filter>
        <filter id="lp-rw-ink-colour" x="-20%" y="-20%" width="140%" height="140%">
          <feMorphology operator="dilate" radius={(props.fontSize * 0.08).toFixed(2)} />
        </filter>
      </defs>
    </svg>
  );
}

export function RotatingWord(props: Readonly<{ fontSize: number }>): JSX.Element {
  return (
    <>
      <style>{CSS}</style>
      <InkFilters fontSize={props.fontSize} />
      <span className="lp-rw" style={{ fontSize: props.fontSize }} aria-hidden="true" data-testid="hero-rotating-word">
        <span className="words">
          {ROTATING_WORDS.map((entry, index) => (
            <span
              className={`w w${index} look-${entry.look}`}
              data-word={entry.word}
              key={entry.word}
              style={{ "--c": AGENT_HEX[entry.colour], "--d": DARK[entry.colour] } as CSSProperties}
            >
              {entry.look === "cloud" || entry.look === "crash" || entry.look === "flash" ? <span className="back" /> : null}

              {entry.look === "burst" || entry.look === "star" || entry.look === "window" ? <span className="under" /> : null}
              <span className="shape" />
              <span className="over" />
              <Decorations look={entry.look} />
              <SfxText word={entry.word} lettering={entry.lettering} motion={MOTION[entry.look]} />
            </span>
          ))}
        </span>
      </span>
    </>
  );
}
