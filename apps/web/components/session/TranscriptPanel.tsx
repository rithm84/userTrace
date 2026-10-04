"use client";

import { ArrowDown } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";

import type { AgentCharacterColour } from "@/lib/api/schemas";
import type { TranscriptTurn } from "@/lib/session/transcript";

/** How close to the bottom still counts as following the conversation. */
const FOLLOW_SLACK_PX = 48;

/**
 * The live transcript beside the call. It follows the newest words unless the
 * reader has scrolled back, and then offers a way back to them instead of
 * pulling the page out from under their eyes.
 */
export function TranscriptPanel(
  props: Readonly<{
    turns: readonly TranscriptTurn[];
    agentName: string;
    agentColour: AgentCharacterColour;
    live: boolean;
  }>,
): JSX.Element {
  const list = useRef<HTMLOListElement>(null);
  const [following, setFollowing] = useState(true);
  const [unread, setUnread] = useState(false);
  const { turns } = props;
  const lastText = turns.at(-1)?.text ?? "";

  useLayoutEffect(() => {
    const element = list.current;
    if (element === null) return;
    if (following) {
      element.scrollTop = element.scrollHeight;
    }
  }, [turns.length, lastText, following]);

  // New words while the reader is scrolled back; reading `following` through a
  // ref keeps scrolling away from the bottom from counting as news.
  const followingRef = useRef(following);
  useEffect(() => {
    if (!followingRef.current && turns.length > 0) {
      setUnread(true);
    }
  }, [turns.length, lastText]);

  const follow = (next: boolean): void => {
    followingRef.current = next;
    setFollowing(next);
    if (next) setUnread(false);
  };

  const onScroll = (): void => {
    const element = list.current;
    if (element === null) return;
    follow(element.scrollHeight - element.scrollTop - element.clientHeight <= FOLLOW_SLACK_PX);
  };

  return (
    <aside className="ss-transcript" aria-label="Transcript" data-testid="session-transcript">
      <header className="ss-transcript-head">
        <h2 className="ss-transcript-title">Transcript</h2>
        <p className="pr-note">Live captions of the call. They can trail a moment behind what was said.</p>
      </header>

      {turns.length === 0 ? (
        <p className="ss-transcript-empty" data-testid="session-transcript-empty">
          What you and {props.agentName} say will appear here as you talk.
        </p>
      ) : (
        <ol
          ref={list}
          className="ss-turns"
          onScroll={onScroll}
          aria-live="polite"
          aria-relevant="additions text"
          data-testid="session-transcript-turns"
        >
          {turns.map((turn, index) => {
            const isAgent = turn.speaker === "agent";
            const isLatest = index === turns.length - 1;
            return (
              <li
                key={turn.key}
                className="ss-turn"
                data-speaker={turn.speaker}
                data-testid={`session-transcript-turn-${String(index)}`}
              >
                <span className="ss-turn-who">
                  <i
                    aria-hidden="true"
                    className="ss-turn-dot"
                    style={isAgent ? { background: `var(--cast-${props.agentColour})` } : undefined}
                  />
                  {isAgent ? props.agentName : "You"}
                </span>
                <p className="ss-turn-text">
                  {turn.text}
                  {props.live && isLatest ? <span className="ss-caret" aria-hidden="true" /> : null}
                </p>
              </li>
            );
          })}
        </ol>
      )}

      {unread ? (
        <button
          type="button"
          className="ss-jump"
          onClick={() => follow(true)}
          data-testid="session-transcript-jump"
        >
          <ArrowDown size={15} aria-hidden="true" />
          Jump to latest
        </button>
      ) : null}
    </aside>
  );
}
