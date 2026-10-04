"use client";

import { useActionState, type JSX } from "react";

import { staffSignIn } from "@/lib/auth/actions";
import { initialAuthFormState, type AuthFormState } from "@/lib/auth/form-state";

/**
 * Two steps in one form: request a one-time code, then verify it. The submit
 * button carries the step, so a resubmission can never be mistaken for the
 * other step.
 */
export function StaffSignInForm(props: Readonly<{ nextPath: string; reason: string | null }>): JSX.Element {
  const [state, formAction, pending] = useActionState<AuthFormState, FormData>(
    staffSignIn,
    initialAuthFormState,
  );
  const codeSent = state.status === "code_sent";

  return (
    <form action={formAction} className="pr-stack" data-testid="staff-sign-in-form" data-status={state.status}>
      <input type="hidden" name="next" value={props.nextPath} />
      {props.reason === null ? null : (
        <p className="pr-message" data-testid="staff-sign-in-reason">
          {props.reason}
        </p>
      )}
      <label className="pr-field">
        <span className="pr-label">Work email</span>
        <input
          type="email"
          name="email"
          autoComplete="email"
          required
          readOnly={codeSent}
          defaultValue={state.email ?? ""}
          data-testid="staff-sign-in-email"
        />
      </label>
      {codeSent ? (
        <label className="pr-field">
          <span className="pr-label">Six-digit code</span>
          <input
            type="text"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            required
            data-testid="staff-sign-in-code"
          />
        </label>
      ) : null}
      {state.message === null ? null : (
        <p className="pr-message pr-message--quiet" role="status" data-testid="staff-sign-in-message">
          {state.message}
        </p>
      )}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button
          type="submit"
          name="intent"
          value={codeSent ? "verify_code" : "request_code"}
          className="pr-btn"
          disabled={pending}
          data-testid="staff-sign-in-submit"
        >
          {codeSent ? "Sign in" : "Email me a code"}
        </button>
        {codeSent ? (
          <button
            type="submit"
            name="intent"
            value="request_code"
            className="pr-btn pr-btn--secondary"
            disabled={pending}
            data-testid="staff-sign-in-resend"
          >
            Send another code
          </button>
        ) : null}
      </div>
    </form>
  );
}
