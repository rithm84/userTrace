"use client";

import { useActionState, useEffect, type JSX } from "react";

import { startDiscordSignIn } from "@/lib/auth/actions";
import { initialDiscordSignInState, type DiscordSignInState } from "@/lib/auth/form-state";

/**
 * Discord sign-in for an interview participant. The invitation is bound to a
 * Discord identity server-side, so this is the only way to claim one. The
 * server prepares the authorization URL; the browser navigates to it.
 */
export function ParticipantSignInForm(
  props: Readonly<{ nextPath: string; reason: string | null }>,
): JSX.Element {
  const [state, formAction, pending] = useActionState<DiscordSignInState, FormData>(
    startDiscordSignIn,
    initialDiscordSignInState,
  );

  useEffect(() => {
    if (state.authorizeUrl !== null) {
      window.location.assign(state.authorizeUrl);
    }
  }, [state.authorizeUrl]);

  return (
    <form action={formAction} className="pr-stack" data-testid="participant-sign-in-form">
      <input type="hidden" name="next" value={props.nextPath} />
      {props.reason === null ? null : (
        <p className="pr-message" data-testid="participant-sign-in-reason">
          {props.reason}
        </p>
      )}
      {state.message === null ? null : (
        <p className="pr-message" role="alert" data-testid="participant-sign-in-message">
          {state.message}
        </p>
      )}
      <button type="submit" className="pr-btn" disabled={pending} data-testid="participant-sign-in-submit">
        Continue with Discord
      </button>
    </form>
  );
}
