"use server";

import { z } from "zod";

import { ControlPlaneError } from "@/lib/api/errors";
import { requireParticipant } from "@/lib/auth/guards";
import { USER_SETTINGS_PATH } from "@/lib/routes";

import type { PreferencesFormState } from "./preferences-state";

const revisionSchema = z.coerce.number().int().nonnegative();

/**
 * Saves the participant's visual guidance and agent cursor preferences.
 *
 * The control plane applies the change only at the revision this page read; a
 * refusal comes back as a message the participant can act on.
 */
export async function savePreferences(
  previous: PreferencesFormState,
  formData: FormData,
): Promise<PreferencesFormState> {
  const revision = revisionSchema.safeParse(formData.get("expected_revision"));
  if (!revision.success) {
    return { ...previous, status: "refused", message: "This form is out of date. Reload the page and try again." };
  }

  const { api } = await requireParticipant(USER_SETTINGS_PATH);
  try {
    const preferences = await api.updateParticipantPreferences({
      expected_revision: revision.data,
      visual_guidance_enabled: formData.get("visual_guidance_enabled") === "on",
      agent_cursor_enabled: formData.get("agent_cursor_enabled") === "on",
    });
    return { status: "saved", message: "Saved.", preferences };
  } catch (error) {
    if (error instanceof ControlPlaneError) {
      return { ...previous, status: "refused", message: describe(error) };
    }
    throw error;
  }
}

function describe(error: ControlPlaneError): string {
  if (error.code === "conflict") {
    return "Your preferences changed somewhere else since this page loaded. Reload to see them, then try again.";
  }
  if (error.code === "transport" || error.code === "unavailable") {
    return "uTrace could not be reached, so nothing changed. Try again in a moment.";
  }
  return error.message;
}
