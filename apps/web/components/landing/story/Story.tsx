"use client";

import type { JSX } from "react";

import { Caption } from "../pieces";
import { BEATS, JOIN_INDEX, STORY_DURATION, agentOnScreen, beatProgress, chapterProgress, selectBeat, type Beat, type ChapterId } from "./beats";
import { BrowserFrame } from "./BrowserFrame";
import { ChapterRail } from "./ChapterRail";
import { CaseFileWindow } from "./CaseFileWindow";
import { DiscordTab } from "./DiscordTab";
import { PreviewTab } from "./PreviewTab";
import { SessionTab } from "./SessionTab";
import { WorkspaceWindow } from "./WorkspaceWindow";
import { SHOPALYTICS } from "./shopalyticsFixture";
import "./story.css";

/** The demo's fixed design page, fitted to the screen with CSS zoom. */
export const PAGE = Object.freeze({ width: 1200, height: 800 });
/** The frame inside the page: the chapter rail on the left, one browser window taking the rest. */
export const FRAME = Object.freeze({ left: 170, top: 40, width: 1022, height: 748, bodyWidth: 1016, tabbedBodyHeight: 658, windowBodyHeight: 698 });

export type StoryProps = Readonly<{
  /** Milliseconds into the demo's timeline; past its end it holds the finished frame. */
  elapsed: number;
  /** The rigs run while the demo plays on screen. */
  active: boolean;
  /** Takes the demo to a chapter. */
  onChapter: (id: ChapterId) => void;
  /** Join interview, pressed by the reader: on into the interview. */
  onJoin: () => void;
}>;

/**
 * The demo page: one browser window, from Maya's request in Discord through the interview's
 * session and preview tabs to uTrace's workspace and Case File windows, over six chapters
 * (Request, Interview, Build, Validate, Iterate, Ship) with the chapter rail down the left and
 * one narration box over the frame's corner.
 */
export function Story(props: StoryProps): JSX.Element {
  const elapsed = Math.min(props.elapsed, STORY_DURATION);
  const index = selectBeat(elapsed);
  const beat = BEATS[index];
  const progress = beatProgress(elapsed, index);
  const playback = elapsed >= STORY_DURATION ? "finished" : "playing";
  return (
    <div className="st-page" data-testid="story-page" data-beat={beat.id} data-chapter={beat.chapter} data-playback={playback} data-elapsed={Math.round(elapsed)} style={{ width: PAGE.width, height: PAGE.height }}>
      <p className="st-note">Maya runs growth at Dudulemon, an activewear brand that uses Shopalytics for its storefront.</p>
      <Frame beat={beat} index={index} progress={progress} active={props.active} onJoin={props.onJoin} />
      <Caption on key={beat.id} style={{ right: -6, top: 22, width: 340 }} testId="story-caption">
        {beat.caption}
      </Caption>
      <ChapterRail beatIndex={index} progress={chapterProgress(elapsed, beat.chapter)} playback={playback} agent={agentOnScreen(beat, progress)} active={props.active} onChapter={props.onChapter} />
    </div>
  );
}

function Frame(props: Readonly<{ beat: Beat; index: number; progress: number; active: boolean; onJoin: () => void }>): JSX.Element {
  const { beat, progress, active } = props;
  // the cast appears where the product would show it: the session tab and the preview tab; the rail carries it elsewhere
  const version = beat.chapter === "iterate" || beat.chapter === "ship" ? SHOPALYTICS.implementation.repairPreviewVersion : SHOPALYTICS.implementation.previewVersion;
  return (
    <div className="st-frame-slot" style={{ left: FRAME.left, top: FRAME.top, width: FRAME.width, height: FRAME.height }}>
      <BrowserFrame surface={beat.surface} joined={props.index >= JOIN_INDEX} discordView={beat.id === "request" || beat.id === "signal" ? "channel" : "dm"} previewVersion={version}>
        {beat.surface === "discord" ? <DiscordTab beat={beat} progress={progress} width={FRAME.bodyWidth} onJoin={props.onJoin} /> : null}
        {beat.surface === "session" ? <SessionTab beat={beat} progress={progress} active={active} /> : null}
        {beat.surface === "preview" ? <PreviewTab beat={beat} progress={progress} active={active} width={FRAME.bodyWidth} height={FRAME.tabbedBodyHeight} /> : null}
        {beat.surface === "workspace" ? <WorkspaceWindow beat={beat} progress={progress} /> : null}
        {beat.surface === "casefile" ? <CaseFileWindow beat={beat} progress={progress} /> : null}
      </BrowserFrame>
    </div>
  );
}
