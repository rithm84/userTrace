"use client";

import { useActionState, type JSX } from "react";

import { authorizeOffersBudget } from "@/lib/dashboard/actions";
import { initialBudgetState, type BudgetState } from "@/lib/dashboard/form-state";

/**
 * The owner-only Outreach Offers allowance. Admins see the figure and the
 * reason they cannot change it; the server re-checks the role regardless.
 */
export function BudgetForm(
  props: Readonly<{ authorizedMajorUnits: number; currency: string; ownerOnlyBlocked: boolean }>,
): JSX.Element {
  const [state, formAction, pending] = useActionState<BudgetState, FormData>(
    authorizeOffersBudget,
    initialBudgetState,
  );

  if (props.ownerOnlyBlocked) {
    return (
      <p className="pr-message pr-message--quiet" data-testid="budget-owner-only">
        Only the client owner can authorize or increase the issuance allowance.
      </p>
    );
  }

  return (
    <form action={formAction} className="pr-stack" data-testid="budget-form" data-status={state.status}>
      <input type="hidden" name="currency" value={props.currency} />
      <label className="pr-field">
        <span className="pr-label">Authorized issuance allowance ({props.currency})</span>
        <input
          type="text"
          name="authorized_major_units"
          inputMode="numeric"
          pattern="[0-9]+"
          required
          defaultValue={String(props.authorizedMajorUnits)}
          data-testid="budget-amount"
        />
      </label>
      <p className="pr-note">
        This is a ceiling on the credits uTrace may issue, not money deposited with uTrace.
        Reserved and earned amounts keep consuming it.
      </p>
      {state.message === null ? null : (
        <p className="pr-message pr-message--quiet" role="status" data-testid="budget-message">
          {state.message}
        </p>
      )}
      <button type="submit" className="pr-btn pr-btn--sm" disabled={pending} data-testid="budget-submit">
        Authorize allowance
      </button>
    </form>
  );
}
