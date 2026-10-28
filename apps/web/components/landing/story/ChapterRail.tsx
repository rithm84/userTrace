"use client";

import type { CharacterState } from "@utrace/agent-character";
import { Check } from "lucide-react";
import type { CSSProperties, JSX } from "react";

import type { AgentColour } from "../characters/AgentSprite";
import { Cameo, ENTRANCE_MS } from "../pieces";
import { BEATS, CHAPTERS, type ChapterId } from "./beats";

/**
 * Six chapter stops centered on one line, with labels close to each marker's edge. The active character matches the
 * window, surrounded by its chapter's progress ring (`progress`, 0..1 through the chapter on the demo's clock) and
 * local beat count, and each stop is a button that jumps to its chapter (`onChapter`). The connecting line records
 * reached chapters; checks mark completed chapters.
 */
export function ChapterRail(props: Readonly<{ beatIndex: number; progress: number; playback: 'playing' | 'finished'; agent: Readonly<{ colour: AgentColour; state: CharacterState }>; active: boolean; onChapter: (id: ChapterId) => void }>): JSX.Element {
  const beat = BEATS[props.beatIndex];
  if (beat === undefined) throw new RangeError(`Unknown story beat index ${props.beatIndex}.`);
  const index = CHAPTERS.findIndex((chapter) => chapter.id === beat.chapter);
  if (index === -1) throw new Error(`Unknown story chapter ${beat.chapter}.`);
  const chapterBeats = BEATS.filter((candidate) => candidate.chapter === beat.chapter);
  const beatNumber = chapterBeats.findIndex((candidate) => candidate.id === beat.id) + 1;
  const percent = Math.floor(props.progress * 100);
  return (
    <ol className="st-rail" data-testid="story-rail" data-chapter={beat.chapter} aria-label="Story chapters" style={{ '--rail-reached': `${index / (CHAPTERS.length - 1) * 100}%` } as CSSProperties}>
      {CHAPTERS.map((chapter, at) => (
        <li key={chapter.id} className={"st-stop" + (at === index ? " is-active" : at < index ? " is-done" : "")} data-testid={"story-stop-" + chapter.id} data-status={at < index || (at === index && props.playback === 'finished') ? 'complete' : at === index ? 'current' : 'upcoming'} aria-current={at === index ? 'step' : undefined} style={{ top: `${(at / (CHAPTERS.length - 1)) * 100}%` }}>
          <span className="st-stop-icon">
            <span className="st-stop-mark" aria-hidden="true">
              {at < index ? <Check size={15} strokeWidth={3} /> : null}
              {at === index ? <>
                <svg className="st-stop-ring" viewBox="0 0 86 86" fill="none">
                  <circle className="st-stop-ring-track" cx={43} cy={43} r={36} strokeWidth={6} />
                  <circle className="st-stop-ring-fill" cx={43} cy={43} r={36} strokeWidth={6} pathLength={100} strokeDasharray={100} strokeDashoffset={100 * (1 - props.progress)} transform="rotate(-90 43 43)" />
                </svg>
                <Cameo key={props.agent.colour} colour={props.agent.colour} state={props.agent.state} park={props.agent.state === "ship" ? SHIP_HOLD_MS : props.active ? null : ENTRANCE_MS} size={66} gazeX={0.5} gazeY={-0.2} testId="story-rail-agent" style={{ position: "relative" }} />
              </> : null}
            </span>
          </span>
          <span className="st-stop-copy">
            <span className="st-stop-label">{chapter.label}</span>
            {at !== index ? <span className="sr-only">{at < index ? 'Complete' : 'Upcoming'}</span> : null}
            {at === index ? <>
              <span className="st-stop-detail" aria-live="polite" aria-atomic="true">
                {props.playback === 'finished' ? <span data-testid="story-rail-status">Complete</span> : <span data-testid="story-rail-beats">{beatNumber} of {chapterBeats.length}</span>}
              </span>
              <span className="sr-only" role="progressbar" aria-label={`${chapter.label} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={props.playback === 'finished' ? 'Complete' : `Beat ${beatNumber} of ${chapterBeats.length}, ${percent}%`} data-testid="story-chapter-progress" />
            </> : null}
          </span>
          <JumpToChapter id={chapter.id} label={chapter.label} onChapter={props.onChapter} />
        </li>
      ))}
    </ol>
  );
}

/**
 * The stop itself, as a button over its marker and label, taking the reader to the chapter's
 * start. A pointer click lets go of focus, since the demo plays on without it; a key press keeps it.
 */
function JumpToChapter(props: Readonly<{ id: ChapterId; label: string; onChapter: (id: ChapterId) => void }>): JSX.Element {
  const { id, onChapter } = props;
  return <button type="button" className="st-stop-jump" data-testid={"story-jump-" + id} aria-label={`Go to the ${props.label} chapter`} onClick={(event) => {
    onChapter(id);
    if (event.detail > 0) event.currentTarget.blur();
  }} />;
}

/**
 * Into the rig's ship state: the satchel has just landed and the wind-up has not started. The
 * state is a one-way exit (satchel on, wind up, dash off, then an empty canvas until the state
 * changes), so the rail never plays it through: its badge would sit empty. It freezes the state
 * on this opening pose, satchel in hand, whenever the ship chapter is on the rail.
 */
const SHIP_HOLD_MS = 250;
