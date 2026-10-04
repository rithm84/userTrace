import type { ParticipantPreferences } from "@/lib/api/schemas";

/**
 * Form state for the participant preferences action. It carries the
 * preferences the control plane last confirmed, so the next save names the
 * revision it actually read.
 */
export type PreferencesFormState = Readonly<{
  status: "idle" | "saved" | "refused";
  message: string | null;
  preferences: ParticipantPreferences;
}>;
