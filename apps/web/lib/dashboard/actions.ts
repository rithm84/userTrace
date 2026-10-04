"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";

import { ControlPlaneError } from "@/lib/api/errors";
import { isOwner, requireStaff } from "@/lib/auth/guards";
import { casePath, DASHBOARD_SETTINGS_PATH } from "@/lib/routes";

import type { BudgetState, StopCaseState } from "./form-state";

const productSignalIdSchema = z.string().min(1).max(128);
const reasonSchema = z.string().trim().min(10).max(1000);

/**
 * The audited, deny-only **Stop this case** control.
 *
 * It withdraws future authority for one Product Signal and records the reason.
 * It cannot approve work, rewrite evidence, resume a case, or revoke an output
 * that was already published, so the only input is the reason and an explicit
 * confirmation. Authority is re-checked here rather than trusted from the page
 * that rendered the button.
 */
export async function stopCase(
  _previous: StopCaseState,
  formData: FormData,
): Promise<StopCaseState> {
  const productSignalId = productSignalIdSchema.safeParse(formData.get("product_signal_id"));
  if (!productSignalId.success) {
    return { status: "error", message: "That case could not be identified." };
  }
  if (formData.get("confirm") !== "stop") {
    return { status: "error", message: "Tick the confirmation to stop this case." };
  }
  const reason = reasonSchema.safeParse(formData.get("reason"));
  if (!reason.success) {
    return {
      status: "error",
      message: "Give a reason of at least ten characters. It is recorded with your identity.",
    };
  }

  const { clientId, api } = await requireStaff(casePath(productSignalId.data));
  try {
    await api.stopCase(clientId, productSignalId.data, reason.data);
  } catch (error) {
    if (error instanceof ControlPlaneError) {
      return { status: "error", message: `The case was not stopped: ${error.message}` };
    }
    throw error;
  }

  // The refreshed case shows the audited stop the control plane recorded; this
  // control is gone from it, because there is no authority left to withdraw.
  revalidatePath(casePath(productSignalId.data));
  return { status: "idle", message: null };
}

const budgetSchema = z.coerce.number().int().nonnegative().max(100_000_000);
const currencySchema = z.string().trim().length(3).regex(/^[A-Z]{3}$/u);

/**
 * Raises or lowers the Outreach Offers issuance allowance.
 *
 * Only the client owner may authorize an allowance, so the role is re-checked
 * here. The control plane still owns the invariant that a budget edit cannot
 * cancel allowance that outstanding promises consume.
 */
export async function authorizeOffersBudget(
  _previous: BudgetState,
  formData: FormData,
): Promise<BudgetState> {
  const major = budgetSchema.safeParse(formData.get("authorized_major_units"));
  if (!major.success) {
    return { status: "error", message: "Enter the allowance as a whole number of currency units." };
  }
  const currency = currencySchema.safeParse(formData.get("currency"));
  if (!currency.success) {
    return { status: "error", message: "The allowance needs the billing currency it is set in." };
  }

  const { clientId, client, api } = await requireStaff(DASHBOARD_SETTINGS_PATH);
  if (!isOwner(client)) {
    return { status: "error", message: "Only the client owner can authorize the issuance allowance." };
  }

  try {
    await api.authorizeOffersBudget(clientId, {
      currency: currency.data,
      authorized_minor_units: major.data * 100,
    });
  } catch (error) {
    if (error instanceof ControlPlaneError) {
      return { status: "error", message: `The allowance was not changed: ${error.message}` };
    }
    throw error;
  }

  revalidatePath(DASHBOARD_SETTINGS_PATH);
  return { status: "saved", message: "The authorized issuance allowance is updated." };
}
