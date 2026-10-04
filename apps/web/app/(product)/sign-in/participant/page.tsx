import type { Metadata } from "next";
import type { JSX } from "react";

import { ParticipantSignInForm } from "@/components/auth/ParticipantSignInForm";
import { ProductShell } from "@/components/product/ProductShell";
import { readNextPath, readReason, type SearchParams } from "@/lib/query";
import { USER_SETTINGS_PATH } from "@/lib/routes";

export const metadata: Metadata = { title: "Sign in with Discord — uTrace" };

export default async function ParticipantSignInPage(
  props: Readonly<{ searchParams: Promise<SearchParams> }>,
): Promise<JSX.Element> {
  const params = await props.searchParams;

  return (
    <ProductShell main="contained" links={[]} current={null} viewerLabel={null}>
      <div className="pr-stack" style={{ maxWidth: 440 }} data-testid="participant-sign-in">
        <div>
          <h1 className="pr-h1">Sign in with Discord</h1>
          <p className="pr-lede">
            Your invitation is bound to the Discord account it was sent to, so that is the account
            that can open the session.
          </p>
        </div>
        <ParticipantSignInForm
          nextPath={readNextPath(params, USER_SETTINGS_PATH)}
          reason={readReason(params)}
        />
      </div>
    </ProductShell>
  );
}
