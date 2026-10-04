"use client";

import { X } from "lucide-react";
import { useRef, type CSSProperties, type JSX } from "react";

import { SHOPALYTICS, type FilterChip } from "../../story/shopalyticsFixture";
import { Balloon, CursorArrow, WallCalendar } from "../art";
import { CUE, passed, stepsSince } from "../cues";
import { quadTransform } from "../homography";
import { Caption, MAYA_ART, MayaArt } from "../pieces";
import { panelBox, type Point } from "../stripGeometry";
import { ShopalyticsDashboard } from "../surfaces/ShopalyticsDashboard";
import { useAnchor } from "../useAnchor";
import { useBrief } from "../useBrief";

const DASHBOARD = Object.freeze({ width: 640, height: 420 });
/** The blank laptop screen in the desk drawing (1400 x 929), measured from its alpha. */
const SCREEN_IN_DRAWING: readonly [Point, Point, Point, Point] = [[714.6, 264.3], [1062.8, 316.3], [1009, 566], [664.5, 494]];
const DESK_WIDTH = MAYA_ART.desk.width;
/** Anchor the drawing and its mapped laptop screen to the panel's bottom edge. */
const DESK_ART = Object.freeze({ left: -56, top: panelBox("desk").height - DESK_WIDTH * 929 / 1400, width: DESK_WIDTH });
const CHIPS: readonly FilterChip[] = SHOPALYTICS.filters;
const FILTER_STAGES: readonly (readonly FilterChip[])[] = Array.from({ length: CHIPS.length + 1 }, (_, count) => CHIPS.slice(0, count));
const DESK_SCALE = DESK_ART.width / 1400;
const DESK_QUAD = SCREEN_IN_DRAWING.map(([x, y]) => [DESK_ART.left + x * DESK_SCALE, DESK_ART.top + y * DESK_SCALE] as Point) as [Point, Point, Point, Point];
const DESK_TRANSFORM = quadTransform(DASHBOARD.width, DASHBOARD.height, DESK_QUAD);
const BUST: CSSProperties = { left: -36, top: 130 };

/** Quantised progress: the chart draws on twelve held frames, not a smooth sweep. */
function onTwos(elapsed: number, start: number, duration: number, frames: number): number {
  if (elapsed < start) return 0;
  return Math.min(1, Math.floor(((elapsed - start) / duration) * frames) / frames);
}

/** Panel 1: Maya at her desk, the real dashboard mapped onto the drawn laptop screen. */
export function DeskPanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  const chart = onTwos(props.elapsed, CUE.deskChart, 1600, 12);
  return (
    <>
      <MayaArt piece="desk" style={{ left: DESK_ART.left, top: DESK_ART.top }} />
      <div data-testid="strip-laptop-screen" style={{ position: "absolute", left: 0, top: 0, width: DASHBOARD.width, height: DASHBOARD.height, transformOrigin: "0 0", transform: DESK_TRANSFORM, background: "#fff" }}>
        <ShopalyticsDashboard filters={FILTER_STAGES[0]} chart={chart} flash={false} popLast={false} />
      </div>
      <WallCalendar style={{ position: "absolute", left: 366, top: 92, scale: "0.86", transformOrigin: "0 0" }} />
      <Caption on={passed(props.elapsed, CUE.desk + 250)} style={{ left: 292, top: 8, width: 206 }} testId="strip-caption-desk">
        Monday, 9:00. Shopalytics tells Maya how the store converts.
      </Caption>
    </>
  );
}

/** The dashboard straight on, scaled and offset so the filter bar is the subject. */
function DashboardCrop(props: Readonly<{ filters: readonly FilterChip[]; flash: boolean; popLast: boolean }>): JSX.Element {
  const scale = 1.16;
  return (
    <div style={{ position: "absolute", left: 60 - 150 * scale, top: 18, width: DASHBOARD.width, height: DASHBOARD.height, transformOrigin: "0 0", transform: `scale(${scale})` }}>
      <ShopalyticsDashboard filters={props.filters} chart={1} flash={props.flash} popLast={props.popLast} />
    </div>
  );
}

/** Panel 2: the same seven filters, rebuilt chip by chip on held frames; the cursor rides the Add filter button. */
export function RitualPanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  const count = stepsSince(props.elapsed, CUE.ritualChip, CUE.ritualChipStep, CHIPS.length);
  const landed = CUE.ritualChip + (count - 1) * CUE.ritualChipStep;
  const root = useRef<HTMLDivElement>(null);
  const addFilter = useAnchor(root, ".sh-add", panelBox("ritual").width, count);
  const cursorOn = passed(props.elapsed, CUE.ritualCursor) && count < CHIPS.length;
  return (
    <div ref={root} style={{ position: "absolute", inset: 0 }}>
      <DashboardCrop filters={FILTER_STAGES[count]} flash={false} popLast={count > 0 && props.elapsed - landed < 220} />
      <MayaArt piece="bust-back" style={BUST} />
      {cursorOn && addFilter !== null ? <CursorArrow size={20} style={{ position: "absolute", left: addFilter.x - 4, top: addFilter.y - 2 }} /> : null}
      {passed(props.elapsed, CUE.ritualTally) ? (
        <span className="strip-sfx strip-pop" data-testid="strip-tally" style={{ position: "absolute", left: 292, top: 46 }}>
          ×7
        </span>
      ) : null}
      <Caption on={passed(props.elapsed, CUE.ritual + 250)} style={{ left: 46, top: 8, width: 150 }} testId="strip-caption-ritual">
        Same seven filters. Every week.
      </Caption>
    </div>
  );
}

/** The reset's flash, in real time however the reader scrolls. */
const FLASH_MS = 120;

/** Panel 3: she comes back later; the bar is empty and the chips fall out of the frame. */
export function ResetPanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  const flash = useBrief(props.elapsed >= CUE.resetFlash && props.elapsed < CUE.resetFlash + FLASH_MS, FLASH_MS);
  const tumbling = passed(props.elapsed, CUE.resetTumble);
  const filters = tumbling ? FILTER_STAGES[0] : CHIPS;
  return (
    <>
      <DashboardCrop filters={filters} flash={flash} popLast={false} />
      {tumbling
        ? CHIPS.map((chip, index) => (
            <span
              key={chip.criterion}
              className="sh-chip strip-tumble"
              data-testid="strip-tumble-chip"
              style={{ position: "absolute", left: 62 + (index % 4) * 78, top: 72 + Math.floor(index / 4) * 30, ["--dx" as string]: `${(index % 3) * 34 - 20}px`, ["--rot" as string]: `${(index % 2 === 0 ? 1 : -1) * (18 + index * 7)}deg`, animationDelay: `${index * 60}ms` }}
            >
              {chip.label}
              <X size={12} />
            </span>
          ))
        : null}
      <MayaArt piece="bust-back" style={BUST} />
      {passed(props.elapsed, CUE.resetBalloon) ? <Balloon text="Again?!" width={132} style={{ left: 150, top: 74, rotate: "-4deg" }} testId="strip-balloon" /> : null}
      <Caption on={passed(props.elapsed, CUE.reset + 250)} style={{ left: 46, top: 8, width: 168 }} testId="strip-caption-reset">
        Come back later, and it&rsquo;s gone.
      </Caption>
    </>
  );
}
