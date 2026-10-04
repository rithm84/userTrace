import type { Metadata } from "next";
import type { JSX } from "react";

import { ProductShell } from "@/components/product/ProductShell";
import { ClaimInvitationForm } from "@/components/session/ClaimInvitationForm";
import { requireParticipant } from "@/lib/auth/guards";
import { invitationPath } from "@/lib/routes";

export const metadata: Metadata = { title: "Your uTrace invitation" };

export default async function InvitationPage(
  props: Readonly<{ params: Promise<{ token: string }> }>,
): Promise<JSX.Element> {
  const { token } = await props.params;
  await requireParticipant(invitationPath(token));

  return (
    <ProductShell main="fill" links={[]} current={null} viewerLabel={null}>
      <div className="ss-single" data-testid="invitation">
        <div className="ss-card">
          <h1 className="pr-h1">You&rsquo;re invited to an interview</h1>
          <p className="pr-lede">
            A uTrace agent will ask you short questions while you show the workflow you wrote
            about, in a copy of the product set up just for you. Nothing you do there touches your
            real account.
          </p>
          <p className="pr-note">
            Opening the session checks your browser, the uTrace extension and your microphone
            first. You can stop at any point.
          </p>
          <ClaimInvitationForm token={token} />
        </div>
      </div>
    </ProductShell>
  );
}
