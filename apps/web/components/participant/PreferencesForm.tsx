"use client";

import { useActionState, type JSX } from "react";

import type { ParticipantPreferences } from "@/lib/api/schemas";
import { savePreferences } from "@/lib/participant/actions";
import type { PreferencesFormState } from "@/lib/participant/preferences-state";

const OPTIONS = [
  {
    name: "visual_guidance_enabled",
    label: "Visual guidance",
    detail: "Let the agent point at, circle and highlight things in your preview while it asks about them.",
  },
  {
    name: "agent_cursor_enabled",
    label: "Agent cursor",
    detail: "Show the agent's own cursor moving to what it is talking about.",
  },
] as const;

/** The participant's action preferences, saved together at the revision the page read. */
export function PreferencesForm(
  props: Readonly<{ preferences: ParticipantPreferences }>,
): JSX.Element {
  const [state, formAction, pending] = useActionState<PreferencesFormState, FormData>(savePreferences, {
    status: "idle",
    message: null,
    preferences: props.preferences,
  });

  return (
    <form
      action={formAction}
      className="ua-form"
      data-testid="user-settings-preferences-form"
      data-status={state.status}
      data-revision={state.preferences.revision}
    >
      <input type="hidden" name="expected_revision" value={state.preferences.revision} />
      <ul className="ua-options">
        {OPTIONS.map((option) => (
          <li key={option.name}>
            <label className="ua-option">
              <span>
                <b>{option.label}</b>
                <span className="ua-option-detail">{option.detail}</span>
              </span>
              <input
                type="checkbox"
                role="switch"
                className="ua-switch"
                name={option.name}
                defaultChecked={state.preferences[option.name]}
                key={`${option.name}-${String(state.preferences.revision)}`}
                data-testid={`user-settings-${option.name}`}
              />
            </label>
          </li>
        ))}
      </ul>
      <div className="ua-form-end">
        {state.message === null ? null : (
          <p
            className={state.status === "saved" ? "ua-saved" : "pr-message"}
            role={state.status === "saved" ? "status" : "alert"}
            data-testid="user-settings-preferences-message"
          >
            {state.message}
          </p>
        )}
        <button type="submit" className="pr-btn" disabled={pending} data-testid="user-settings-preferences-save">
          Save preferences
        </button>
      </div>
    </form>
  );
}
