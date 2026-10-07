import Link from "next/link";
import type { JSX } from "react";

import { Chip, EmptyState, formatTimestamp, type ChipTone } from "@/components/product/ui";
import type { ParticipantInterview, SessionEndReason, TabState } from "@/lib/api/schemas";
import { sessionPath } from "@/lib/routes";

const LIVE_LABELS: Readonly<Record<Exclude<TabState, "ended">, string>> = {
  participant_setup: "Setting up",
  interviewing: "In progress",
  paused: "Paused",
  reconnecting: "Reconnecting",
};

const LIVE_TONES: Readonly<Record<Exclude<TabState, "ended">, ChipTone>> = {
  participant_setup: "waiting",
  interviewing: "active",
  paused: "waiting",
  reconnecting: "waiting",
};

const END_LABELS: Readonly<Record<SessionEndReason, string>> = {
  finish_later: "Finished later",
  ended_by_participant: "Ended by you",
  client_stopped: "Stopped by the team",
  completed: "Completed",
  no_implementation: "Completed, no change built",
  blocked: "Stopped by a blocker",
  not_validated: "Did not pass its checks",
};

/** The participant's Interview Sessions, with a way back into any that are still open. */
export function InterviewList(
  props: Readonly<{ interviews: readonly ParticipantInterview[] }>,
): JSX.Element {
  if (props.interviews.length === 0) {
    return (
      <EmptyState testId="user-settings-interviews-empty">
        No interviews yet. When a product team invites you, the invitation arrives as a private
        Discord message and the interview shows up here.
      </EmptyState>
    );
  }

  return (
    <ul className="ua-interviews" data-testid="user-settings-interviews">
      {props.interviews.map((interview) => (
        <li key={interview.session_id} data-testid={`user-settings-interview-${interview.session_id}`}>
          <div className="ua-interview-main">
            <span className="pr-label">
              {interview.client_name} · {interview.purpose === "validation" ? "Validation" : "Interview"}
            </span>
            <span className="ua-interview-topic">{interview.topic}</span>
            <span className="pr-note">Last activity {formatTimestamp(interview.updated_at)} UTC</span>
          </div>
          <div className="ua-interview-end">
            {interview.state === "ended" ? (
              <Chip tone="closed" testId="user-settings-interview-state">
                {interview.end_reason === null ? "Ended" : END_LABELS[interview.end_reason]}
              </Chip>
            ) : (
              <>
                <Chip tone={LIVE_TONES[interview.state]} testId="user-settings-interview-state">
                  {LIVE_LABELS[interview.state]}
                </Chip>
                <Link
                  href={sessionPath(interview.session_id)}
                  className="pr-btn pr-btn--sm"
                  data-testid="user-settings-interview-open"
                >
                  Open session
                </Link>
              </>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
