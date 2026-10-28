"use client";

import type { CharacterState } from "@utrace/agent-character";
import { Clock, ExternalLink, Mic, Pause, PhoneOff } from "lucide-react";
import { useLayoutEffect, useRef, type JSX } from "react";

import { AGENT_HEX } from "../characters/AgentSprite";
import { Cameo, ENTRANCE_MS } from "../pieces";
import { BEATS, PRODUCT_AGENT, agentOnScreen, mayaTurn as isMayaTurn, type Beat } from "./beats";
import { SHOPALYTICS } from "./shopalyticsFixture";

/** The product's status line for each state the story's session beats show. */
const STATUS: Readonly<Partial<Record<CharacterState, string>>> = { speaking: "Speaking", listening: "Listening", thinking: "Thinking...", acknowledgement: "Got it", concern: "Looking into something", completion: "All done" };

type Turn = Readonly<{ key: string; speaker: "agent" | "maya"; text: string }>;

/** The first `fraction` of a line, by words, as the live transcript shows it while it is being said. */
function said(line: string, fraction: number): string {
  const words = line.split(" ");
  return words.slice(0, Math.max(1, Math.ceil(words.length * Math.min(1, Math.max(0, fraction))))).join(" ");
}

/**
 * The call's transcript at this moment: one live call from the greeting on, so everything said
 * in earlier beats in full, including what Maya said while she worked in the preview, then this
 * beat's turns as far as they have been said. The agent speaks first; Maya answers in the
 * beat's second half (`mayaTurn`).
 */
function transcript(beat: Beat, progress: number): readonly Turn[] {
  const turns = (line: Beat, spoken: number): Turn[] => [
    ...(line.agentLine === undefined ? [] : [{ key: `${line.id}-agent`, speaker: "agent", text: said(line.agentLine, line.mayaLine === undefined ? spoken / 0.6 : spoken / 0.45) } as const]),
    ...(line.mayaLine === undefined || !isMayaTurn(line, spoken) ? [] : [{ key: `${line.id}-maya`, speaker: "maya", text: said(line.mayaLine, (spoken - 0.55) / 0.4) } as const]),
  ];
  return BEATS.slice(0, BEATS.indexOf(beat) + 1).flatMap((line) => turns(line, line === beat ? progress : 1));
}

/**
 * The uTrace session tab as the product lays it out: the call on the left (context and
 * connection, the character, its status in display type, Open preview, the four controls) and
 * the live transcript on the right, where every turn names who spoke it and the newest stays in
 * view. The implementation is live, so it is one Interview Session throughout.
 */
export function SessionTab(props: Readonly<{ beat: Beat; progress: number; active: boolean }>): JSX.Element {
  const { beat, progress } = props;
  const state: CharacterState = agentOnScreen(beat, progress).state;
  const turns = transcript(beat, progress);
  const list = useRef<HTMLOListElement>(null);
  const newest = turns.at(-1)?.text ?? "";
  useLayoutEffect(() => {
    // the product follows the newest words; the demo's reader never scrolls the column back
    const element = list.current;
    if (element !== null) element.scrollTop = element.scrollHeight;
  }, [turns.length, newest]);
  return (
    <div className="st-session" data-testid="story-session" data-state={state}>
      <section className="st-session-stage" aria-label="Call">
        <div className="st-session-head">
          <span>
            {SHOPALYTICS.client} · Interview session
          </span>
          <span className="st-pill">
            <i />
            Connected
          </span>
        </div>
        <div className="st-session-center">
          <Cameo colour={PRODUCT_AGENT.colour} state={state} park={props.active ? null : ENTRANCE_MS} size={200} gazeX={0} gazeY={0.1} testId="story-session-agent" style={{ position: "relative" }} />
          <p className="st-status">{STATUS[state] ?? "Listening"}</p>
          <span className="st-session-preview">
            Open preview
            <ExternalLink size={15} />
          </span>
        </div>
        <div className="st-controls" aria-hidden="true">
          <span>
            <Mic size={16} />
            Mute
          </span>
          <span>
            <Pause size={16} />
            Pause
          </span>
          <span>
            <Clock size={16} />
            Finish later
          </span>
          <span className="is-end">
            <PhoneOff size={16} />
            End interview
          </span>
        </div>
      </section>
      <aside className="st-transcript" aria-label="Transcript" data-testid="story-transcript">
        <header>
          <strong>Transcript</strong>
          <p>Live captions of the call.</p>
        </header>
        <ol ref={list}>
          {turns.map((turn, index) => (
            <li key={turn.key} className="st-turn" data-speaker={turn.speaker} data-testid={`story-turn-${turn.key}`}>
              <span className="st-turn-who">
                <i aria-hidden="true" style={turn.speaker === "agent" ? { background: AGENT_HEX[PRODUCT_AGENT.colour] } : undefined} />
                {turn.speaker === "agent" ? PRODUCT_AGENT.name : "Maya"}
              </span>
              <p>
                {turn.text}
                {index === turns.length - 1 ? <span className="st-caret" aria-hidden="true" /> : null}
              </p>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
