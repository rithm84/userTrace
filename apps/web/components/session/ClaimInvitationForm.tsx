"use client";

import { useActionState, type JSX } from "react";

import { initialClaimState, type ClaimState } from "@/lib/session/claim-state";
import { claimInvitation } from "@/lib/session/invitation-actions";

/** Claiming is a write, so it happens on a press rather than on a page view. */
export function ClaimInvitationForm(props: Readonly<{ token: string }>): JSX.Element {
  const [state, formAction, pending] = useActionState<ClaimState, FormData>(
    claimInvitation,
    initialClaimState,
  );

  return (
    <form action={formAction} className="pr-stack" data-testid="claim-form" data-status={state.status}>
      <input type="hidden" name="token" value={props.token} />
      {state.message === null ? null : (
        <p className="pr-message" role="alert" data-testid="claim-message">
          {state.message}
        </p>
      )}
      <button type="submit" className="pr-btn pr-btn--wide" disabled={pending} data-testid="claim-submit">
        Open my session
      </button>
    </form>
  );
}
