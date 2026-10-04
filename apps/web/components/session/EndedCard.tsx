import Link from "next/link";
import type { JSX } from "react";

import type { SessionEndReason } from "@/lib/api/schemas";
import { USER_SETTINGS_PATH } from "@/lib/routes";

const REASONS: Readonly<Record<SessionEndReason, string>> = {
  finish_later:
    "Saved where you left it. Come back from your uTrace account, or from the private link uTrace sends you, and pick it up with the same agent.",
  ended_by_participant:
    "The interview is over and nothing further will run on this case. Thanks for the time you gave it.",
  client_stopped:
    "The product team stopped this case. Nothing further will run, and anything you had already completed still counts.",
  completed:
    "You validated the change. The product team gets a Case File and a draft pull request; the merge is theirs to make.",
  blocked:
    "uTrace hit something it could not get past and stopped. The product team can see what happened.",
  not_validated:
    "The new version did not pass every check you agreed, so nothing ships from this session. The product team gets what you found, and the time you gave still counts.",
};

/** The ended state, and the way back out of the session tab. */
export function EndedCard(
  props: Readonly<{ reason: SessionEndReason | null; returned: boolean; onReturn: () => void }>,
): JSX.Element {
  if (props.returned) {
    return (
      <div className="ss-card" data-testid="session-returned">
        <h1 className="pr-h1">You&rsquo;re all set</h1>
        <p className="pr-lede">
          You can close this tab and the preview tab. Everything uTrace needed is recorded.
        </p>
        <Link href={USER_SETTINGS_PATH} className="pr-btn pr-btn--wide ss-card-action" data-testid="session-open-settings">
          Your uTrace account
        </Link>
      </div>
    );
  }

  return (
    <div className="ss-card" data-testid="session-ended" data-reason={props.reason ?? "unknown"}>
      <h1 className="pr-h1">Session ended</h1>
      <p className="pr-lede" data-testid="session-ended-reason">
        {props.reason === null
          ? "This session has ended. Nothing further is running in this tab."
          : REASONS[props.reason]}
      </p>
      <button type="button" className="pr-btn pr-btn--wide ss-card-action" onClick={props.onReturn} data-testid="session-return">
        Back to uTrace
      </button>
    </div>
  );
}
