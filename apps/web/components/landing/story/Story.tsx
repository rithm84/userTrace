"use client";

import type { MotionValue } from "motion/react";
import type { JSX } from "react";

import { Caption } from "../strip/pieces";
import { PAGE } from "../strip/stripGeometry";
import { BEATS, CHAPTERS, STORY_DURATION, agentOnScreen, beatProgress, chapterProgress, lastBeatOf, selectBeat, type Beat, type ChapterId } from "./beats";
import { BrowserFrame } from "./BrowserFrame";
import { ChapterRail } from "./ChapterRail";
import { CaseFileWindow } from "./CaseFileWindow";
import { PreviewTab } from "./PreviewTab";
import { SessionTab } from "./SessionTab";
import { WorkspaceWindow } from "./WorkspaceWindow";
import { SHOPALYTICS } from "./shopalyticsFixture";
import "./story.css";

/** The frame inside the 1200 x 800 page: the chapter rail on the left, one browser window taking the rest. */
export const FRAME = Object.freeze({ left: 170, top: 40, width: 1022, height: 748, bodyWidth: 1016, tabbedBodyHeight: 658, windowBodyHeight: 698 });

export type StoryProps = Readonly<{
  /** Milliseconds into the story's timeline; the journey sets it from the reader's scroll. */
  elapsed: number;
  /** The rigs run while the story is on screen. */
  active: boolean;
  /** Px into the story's scroll, which the chapter ring follows as far as the story has drawn; null for the stills. */
  scroll: MotionValue<number> | null;
  /** Takes the reader to a chapter's start; null for the stills. */
  onChapter: ((id: ChapterId) => void) | null;
  reducedMotion: boolean;
}>;

/**
 * The story the strip's Join click opens: one browser window with a uTrace session tab and
 * a Shopalytics preview tab, plus uTrace's workspace and Case File windows, over five chapters
 * (Interview, Build, Validate, Iterate, Ship) on one timeline the reader's scroll plays, with
 * the chapter rail down the left.
 */
export function Story(props: StoryProps): JSX.Element {
  const { elapsed } = props;
  if (props.reducedMotion) return <StoryStills />;
  const index = selectBeat(elapsed);
  const beat = BEATS[index];
  const progress = beatProgress(elapsed, index);
  const playback = elapsed >= STORY_DURATION ? "finished" : "playing";
  return (
    <div className="st-page" data-testid="story-page" data-beat={beat.id} data-chapter={beat.chapter} data-elapsed={Math.round(elapsed)} data-playback={playback} style={{ width: PAGE.width, height: PAGE.height }}>
      <Frame beat={beat} progress={progress} active={props.active} />
      <Caption on key={beat.id} style={{ right: -6, top: 22, width: 340 }} testId="story-caption">
        {beat.caption}
      </Caption>
      <ChapterRail beatIndex={index} progress={chapterProgress(elapsed, beat.chapter)} playback={playback} agent={agentOnScreen(beat, progress)} active={props.active} scroll={props.scroll === null ? null : { px: props.scroll, drawn: elapsed }} onChapter={props.onChapter} />
    </div>
  );
}

function Frame(props: Readonly<{ beat: Beat; progress: number; active: boolean }>): JSX.Element {
  const { beat, progress, active } = props;
  // the cast appears where the product would show it: the session tab and the preview tab; the rail carries it elsewhere
  const version = beat.chapter === "iterate" || beat.chapter === "ship" ? SHOPALYTICS.implementation.repairPreviewVersion : SHOPALYTICS.implementation.previewVersion;
  return (
    <div className="st-frame-slot" style={{ left: FRAME.left, top: FRAME.top, width: FRAME.width, height: FRAME.height }}>
      <BrowserFrame surface={beat.surface} previewVersion={version}>
        {beat.surface === "session" ? <SessionTab beat={beat} progress={progress} active={active} /> : null}
        {beat.surface === "preview" ? <PreviewTab beat={beat} progress={progress} active={active} width={FRAME.bodyWidth} height={FRAME.tabbedBodyHeight} /> : null}
        {beat.surface === "workspace" ? <WorkspaceWindow beat={beat} progress={progress} /> : null}
        {beat.surface === "casefile" ? <CaseFileWindow beat={beat} progress={progress} /> : null}
      </BrowserFrame>
    </div>
  );
}

/** Reduced motion: each chapter's last beat as a still, stacked in normal flow. */
function StoryStills(): JSX.Element {
  return (
    <div className="st-stills" data-testid="story-stills">
      {CHAPTERS.map((chapter) => {
        const index = lastBeatOf(chapter.id);
        const beat = BEATS[index];
        return (
          <section key={chapter.id} className="st-still" aria-label={chapter.label}>
            <div className="st-page" style={{ width: PAGE.width, height: PAGE.height }}>
              <Frame beat={beat} progress={1} active={false} />
              <Caption on style={{ right: -6, top: 22, width: 340 }}>
                {beat.caption}
              </Caption>
              <ChapterRail beatIndex={index} progress={1} playback="finished" agent={agentOnScreen(beat, 1)} active={false} scroll={null} onChapter={null} />
            </div>
          </section>
        );
      })}
    </div>
  );
}
