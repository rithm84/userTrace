"use client";

import { useActionState, useState, type JSX } from "react";

import { stopCase } from "@/lib/dashboard/actions";
import { initialStopCaseState, type StopCaseState } from "@/lib/dashboard/form-state";

/**
 * Stopping is a denial, so the control asks for a written reason and an
 * explicit confirmation before it submits, and it says plainly what stopping
 * does and does not do.
 *
 * A successful stop needs no acknowledgement here: the case loses its authority
 * to withdraw, so this control disappears and the panel around it shows the
 * audited record the control plane wrote.
 */
export function StopCaseControl(props: Readonly<{ productSignalId: string }>): JSX.Element {
  const [state, formAction, pending] = useActionState<StopCaseState, FormData>(
    stopCase,
    initialStopCaseState,
  );
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button
        type="button"
        className="pr-btn pr-btn--danger pr-btn--sm"
        onClick={() => setOpen(true)}
        data-testid="stop-case-open"
      >
        Stop this case
      </button>
    );
  }

  return (
    <form action={formAction} className="pr-stack" data-testid="stop-case-form">
      <input type="hidden" name="product_signal_id" value={props.productSignalId} />
      <p className="pr-note">
        Stopping withdraws further uTrace authority and notifies the originating user. It cannot
        approve work, change recorded evidence, resume the case, or withdraw an output that has
        already been published.
      </p>
      <label className="pr-field">
        <span className="pr-label">Reason, recorded with your identity</span>
        <textarea name="reason" required minLength={10} data-testid="stop-case-reason" />
      </label>
      <label className="pr-check">
        <input type="checkbox" name="confirm" value="stop" required data-testid="stop-case-confirm" />
        <span>I am withdrawing uTrace&rsquo;s authority for this case.</span>
      </label>
      {state.message === null ? null : (
        <p className="pr-message pr-message--quiet" role="alert" data-testid="stop-case-message">
          {state.message}
        </p>
      )}
      <div style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="pr-btn pr-btn--danger pr-btn--sm" disabled={pending} data-testid="stop-case-submit">
          Stop this case
        </button>
        <button
          type="button"
          className="pr-btn pr-btn--secondary pr-btn--sm"
          onClick={() => setOpen(false)}
          data-testid="stop-case-cancel"
        >
          Keep the case open
        </button>
      </div>
    </form>
  );
}
