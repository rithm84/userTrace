import type { Metadata } from "next";
import type { JSX } from "react";

import { InterviewList } from "@/components/participant/InterviewList";
import { PreferencesForm } from "@/components/participant/PreferencesForm";
import { PARTICIPANT_LINKS, ProductShell } from "@/components/product/ProductShell";
import { requireParticipant } from "@/lib/auth/guards";
import type { ControlPlaneClient } from "@/lib/api/client";
import { ControlPlaneError } from "@/lib/api/errors";
import type { ParticipantAccount } from "@/lib/api/schemas/session";
import { USER_SETTINGS_PATH } from "@/lib/routes";

export const metadata: Metadata = { title: "Your uTrace account" };

/**
 * Where a participant lands after signing in, and the way back from a finished
 * session: who they are signed in as, the interviews they took part in (with a
 * way back into any still open), and their visual-guidance preferences, all
 * from the control plane's participant account read model.
 */
export default async function UserSettingsPage(): Promise<JSX.Element> {
  const { viewer, api } = await requireParticipant(USER_SETTINGS_PATH);
  const account = await participantAccount(api);
  const name = viewer.displayName ?? "Your Discord account";

  return (
    <ProductShell
      main="contained"
      links={PARTICIPANT_LINKS}
      current="user-settings"
      viewerLabel={viewer.displayName}
    >
      <div className="ua" data-testid="user-settings">
        <header className="ua-head">
          <span className="ua-avatar" aria-hidden="true">
            {name.charAt(0).toUpperCase()}
          </span>
          <div className="ua-who">
            <h1 className="pr-h1" data-testid="user-settings-identity-name">
              {name}
            </h1>
            <p className="pr-lede">
              Signed in with Discord. Your identity follows you across every product you are
              interviewed about; what you say in an interview stays with that product&rsquo;s team.
            </p>
          </div>
          <dl className="pr-tile ua-count" data-testid="user-settings-participation">
            <dt>Interviews</dt>
            <dd>{account === null ? 0 : account.participation_count}</dd>
          </dl>
        </header>

        <div className="ua-grid">
          <section className="pr-panel pr-panel--raised" data-testid="user-settings-interviews-panel">
            <div className="pr-panel-head">
              <h2 className="pr-h2">Your interviews</h2>
            </div>
            <InterviewList interviews={account === null ? [] : account.interviews} />
          </section>

          <section className="pr-panel pr-panel--raised" data-testid="user-settings-preferences">
            <div className="pr-panel-head">
              <h2 className="pr-h2">In your preview</h2>
            </div>
            <p className="pr-note ua-panel-lede">
              How the agent may show you things while you work. These apply to every product.
            </p>
            {account === null ? (
              <p className="pr-note" data-testid="user-settings-preferences-pending">
                Your preferences can be set once you open your first interview invitation.
              </p>
            ) : (
              <PreferencesForm preferences={account.preferences} />
            )}
          </section>
        </div>
      </div>
    </ProductShell>
  );
}

/**
 * The participant's account, or `null` before they claim their first invitation.
 * A signed-in person becomes a uTrace participant by claiming one, so until then
 * the control plane has no account to read and refuses the request.
 */
async function participantAccount(api: ControlPlaneClient): Promise<ParticipantAccount | null> {
  try {
    return await api.getParticipantAccount();
  } catch (error) {
    if (error instanceof ControlPlaneError && error.code === "forbidden") {
      return null;
    }
    throw error;
  }
}
