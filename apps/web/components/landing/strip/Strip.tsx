"use client";

import { memo, useEffect, useState, type JSX } from "react";

import { LandingRig } from "../characters/LandingRig";
import { Badge, Burst, FocusLines } from "./art";
import { CAST_SENTENCES, CAST_STARTS, CAST_TYPING, CUE, INTRO_END, INTRO_EXIT, PANEL_CUE, STRIP_DURATION, passed } from "./cues";
import { ClickPanel, InvitePanel, ReplyPanel, RequestPanel } from "./panels/discordPanels";
import { DeskPanel, ResetPanel, RitualPanel } from "./panels/deskPanels";
import { EchoPanel } from "./panels/EchoPanel";
import { Cameo, ENTRANCE_MS, MAYA_ART, mayaArtImage, type MayaPiece } from "./pieces";
import { usePreloadedImages } from "./preloadImages";
import { DISCORD_MARK_IMAGE, avatarImage } from "./surfaces/discord";
import { APP_STORE_MARK_IMAGE, REDDIT_MARK_IMAGE, X_MARK_IMAGE } from "./surfaces/clippings";
import { PAGE, PANELS, panelBox, panelClip, panelPerimeter, panelPoints, type PanelId } from "./stripGeometry";
import { useBrief } from "./useBrief";
import "./strip.css";

export type StripProps = Readonly<{
  /** Milliseconds into the strip's timeline. */
  elapsed: number;
  /** The rigs run: while the clock plays, while the finished page waits, and through the zoom. Parked otherwise. */
  live: boolean;
  reducedMotion: boolean;
  /** Pressing Join interview before the scroll presses it carries the reader on into the story. */
  onJoin: () => void;
}>;

const PANEL_SHAPES = PANELS.map((shape, index) => ({
  id: shape.id,
  box: panelBox(shape.id),
  clip: panelClip(shape.id),
  perimeter: panelPerimeter(shape.id),
  points: panelPoints(shape.id),
  end: index + 1 < PANELS.length ? PANEL_CUE[PANELS[index + 1].id] : STRIP_DURATION,
}));

/**
 * The comic page under the hero: the case before uTrace acts. Eight panels in
 * three tiers with diagonal gutters, drawn panels beside real product crops, the cast
 * on the frame (each with a job in its panel), and one real button at the end. The
 * reader's scroll sets `elapsed`, so every beat plays forward and back with it; the
 * scroll itself presses the button.
 */
/** Every image the panels show, started when the strip mounts so none arrives after its panel. */
const PANEL_IMAGES = [...(Object.keys(MAYA_ART) as MayaPiece[]).map(mayaArtImage), avatarImage(false, 36), avatarImage(true, 36), DISCORD_MARK_IMAGE, REDDIT_MARK_IMAGE, APP_STORE_MARK_IMAGE, X_MARK_IMAGE];

/**
 * The faces the product crops set their text in (Discord's, Reddit's). A face downloads only
 * when text first needs it, which would draw a crop's first frames in a fallback face; the
 * strip loads them as it mounts instead.
 */
const CROP_FONTS: readonly string[] = ['500 14px "Noto Sans Variable"', '500 14px "Reddit Sans Variable"'];

/** Loads the crops' faces now; a face that matches nothing is a broken stylesheet, so it fails loudly. */
function useCropFonts(): void {
  useEffect(() => {
    for (const font of CROP_FONTS) {
      void document.fonts.load(font).then((faces) => {
        if (faces.length === 0) throw new Error(`The strip's crop font ${font} matches no loaded @font-face; check its stylesheet import.`);
      });
    }
  }, []);
}

/** How long coral's realise pose may play: short of the wink at its frame 30 (500 ms). */
const SURPRISE_MS = 280;

export function Strip(props: StripProps): JSX.Element {
  const { elapsed, live, reducedMotion } = props;
  usePreloadedImages(PANEL_IMAGES);
  useCropFonts();
  const finished = elapsed >= STRIP_DURATION;
  const joined = passed(elapsed, CUE.clickPress);
  const burst = passed(elapsed, CUE.requestBurst);
  // the surprise is the first 280 ms of the rig's realise timeline, in real time however the reader scrolls; leaving then (plus the 160 ms blend) never reaches the wink at frame 30
  const realised = useBrief(passed(elapsed, CUE.requestRealise) && elapsed < CUE.requestRealise + SURPRISE_MS, SURPRISE_MS);
  // the cast stays alive while the stage is open, including while the finished page waits for the click
  const rigsActive = live && !reducedMotion;
  const introducing = !reducedMotion && elapsed < INTRO_END;
  const introOn = introducing && elapsed > 0 && elapsed < INTRO_EXIT;
  return (
    <div className="strip-page dc-vars" data-testid="strip-page" data-elapsed={Math.round(elapsed)} data-finished={finished ? "true" : "false"} style={{ width: PAGE.width, height: PAGE.height }}>
      {PANEL_SHAPES.map((shape) => {
        const { box } = shape;
        const introPanel = shape.id === "echo" && introducing;
        const on = passed(elapsed, PANEL_CUE[shape.id]) || (introPanel && introOn);
        return (
          <section key={shape.id} className="strip-panel" data-panel={shape.id} data-on={on ? "true" : "false"} style={{ left: box.left, top: box.top, width: box.width, height: box.height, clipPath: shape.clip }}>
            {introPanel ? <div className="strip-intro" data-testid="strip-intro" style={{ opacity: Math.max(0, Math.min(1, (INTRO_END - elapsed) / (INTRO_END - INTRO_EXIT))) }}><CastLine elapsed={elapsed} /></div> : null}
            <div className={"strip-panel-inner" + (on && !introPanel ? " is-on" : "")}>{!introPanel && (on || reducedMotion) ? <PanelContent id={shape.id} elapsed={Math.min(elapsed, shape.end)} joined={joined} onJoin={props.onJoin} /> : null}</div>
          </section>
        );
      })}

      <svg className="strip-borders" viewBox={`0 0 ${PAGE.width} ${PAGE.height}`} aria-hidden="true">
        {PANEL_SHAPES.map((shape) => {
          const { perimeter } = shape;
          const on = passed(elapsed, PANEL_CUE[shape.id]) || (shape.id === "echo" && introOn);
          return <polygon key={shape.id} points={shape.points} fill="none" stroke="#000" strokeWidth={3} strokeLinejoin="miter" vectorEffect="non-scaling-stroke" strokeDasharray={perimeter} strokeDashoffset={on ? 0 : perimeter} style={{ transition: reducedMotion ? "none" : "stroke-dashoffset 0.45s linear" }} />;
        })}
      </svg>

      <StripCast reset={passed(elapsed, CUE.resetTumble)} echo={passed(elapsed, CUE.echoThinking)} invite={passed(elapsed, CUE.inviteMessage)} click={passed(elapsed, CUE.clickCursor)} burst={burst} realised={realised} active={rigsActive} waiting={passed(elapsed, CUE.clickCursor) && !joined} />
    </div>
  );
}

/** The cast only updates when a cue or its playback state changes. */
const StripCast = memo(function StripCast(props: Readonly<{ reset: boolean; echo: boolean; invite: boolean; click: boolean; burst: boolean; realised: boolean; active: boolean; waiting: boolean }>): JSX.Element {
  const park = props.active ? null : ENTRANCE_MS;
  const nod = useNod(props.waiting, props.active);
  return <>
      {/* mint, a sticker on the reset panel's corner, wincing at the falling chips */}
      <Badge size={92} on={props.reset} style={{ left: 1086, top: 20 }}>
        <Cameo colour="mint" state="concern" park={park} size={72} gazeX={-0.6} gazeY={0.6} testId="strip-mint" style={{ position: "relative" }} />
      </Badge>

      {/* coral bursts out of the request panel: the burst pops, the rig only fades so its canvas is never measured small */}
      <div className={"strip-burst" + (props.burst ? " is-on" : "")} data-testid="strip-burst" style={{ left: -74, top: 232 }}>
        <Burst size={176} className="strip-burst-shape" style={{ position: "absolute", left: 0, top: 0 }} />
        <FocusLines style={{ position: "absolute", left: 132, top: 6 }} />
        <LandingRig colour="coral" pose={props.realised ? "realise" : "investigate"} size={120} gazeX={0.55} gazeY={0.45} active={props.active && props.burst} testId="strip-coral" style={{ position: "absolute", left: 28, top: 28 }} />
      </div>

      {/* lilac, a sticker on the echo panel's top corner, listening to the same complaint from three places */}
      <Badge size={96} on={props.echo} style={{ left: 744, top: 290 }}>
        <Cameo colour="lilac" state="listening" park={park} size={76} gazeX={-0.5} gazeY={0.7} testId="strip-lilac" style={{ position: "relative" }} />
      </Badge>

      {/* sky, a rounded sticker on the invitation panel's bottom-left corner, doing the talking */}
      <Badge size={84} square on={props.invite} style={{ left: -12, top: 722 }}>
        <Cameo colour="sky" state="speaking" park={park} size={76} gazeX={0.8} gazeY={-0.5} testId="strip-sky" style={{ position: "relative" }} />
      </Badge>

      {/* peach, a sticker between the last two panels, nodding toward the button until it is pressed */}
      <Badge size={96} on={props.click} style={{ left: 458, top: 700 }}>
        <Cameo colour="peach" state={nod ? "acknowledgement" : "idle"} park={park} size={78} gazeX={0.8} gazeY={-0.35} testId="strip-peach" style={{ position: "relative" }} />
      </Badge>
  </>;
});

/** The case introduction, typed sentence by sentence in the middle panel. */
function CastLine(props: Readonly<{ elapsed: number }>): JSX.Element {
  const last = CAST_SENTENCES.length - 1;
  const length = (index: number): number => CAST_SENTENCES[index].reduce((sum, segment) => sum + segment.text.length, 0);
  const done = props.elapsed >= CAST_STARTS[last] + length(last) * CAST_TYPING.charMs;
  return (
    <p className="strip-cast" data-testid="strip-cast" data-typed={done ? "true" : "false"} aria-label={CAST_SENTENCES.flat().map((segment) => segment.text).join(" ")}>
      {CAST_SENTENCES.map((sentence, index) => {
        const total = length(index);
        const shown = done ? total : Math.max(0, Math.min(total, Math.floor((props.elapsed - CAST_STARTS[index]) / CAST_TYPING.charMs)));
        if (shown === 0) return null;
        let remaining = shown;
        return (
          <span key={index} data-complete={shown === total ? "true" : "false"}>
            {sentence.map((segment, part) => {
              const take = Math.min(segment.text.length, remaining);
              remaining -= take;
              if (take <= 0) return null;
              const text = segment.text.slice(0, take);
              return segment.bold ? <b key={part}>{text}</b> : <span key={part}>{text}</span>;
            })}
            {shown < total ? <i className="strip-caret" aria-hidden="true" /> : index < last ? " " : null}
          </span>
        );
      })}
    </p>
  );
}

/** While the cursor waits on the button, a nod every few seconds: the acknowledgement state is a one-shot, so it is re-entered from idle. */
function useNod(waiting: boolean, active: boolean): boolean {
  const [nod, setNod] = useState(false);
  useEffect(() => {
    if (!waiting || !active) return;
    const timer = window.setInterval(() => setNod((value) => !value), 2600);
    return () => window.clearInterval(timer);
  }, [waiting, active]);
  return waiting && nod;
}

const PanelContent = memo(function PanelContent(props: Readonly<{ id: PanelId; elapsed: number; joined: boolean; onJoin: () => void }>): JSX.Element {
  switch (props.id) {
    case "desk":
      return <DeskPanel elapsed={props.elapsed} />;
    case "ritual":
      return <RitualPanel elapsed={props.elapsed} />;
    case "reset":
      return <ResetPanel elapsed={props.elapsed} />;
    case "request":
      return <RequestPanel elapsed={props.elapsed} />;
    case "echo":
      return <EchoPanel elapsed={props.elapsed} />;
    case "reply":
      return <ReplyPanel elapsed={props.elapsed} />;
    case "invite":
      return <InvitePanel elapsed={props.elapsed} />;
    case "click":
      return <ClickPanel elapsed={props.elapsed} joined={props.joined} onJoin={props.onJoin} />;
  }
});
