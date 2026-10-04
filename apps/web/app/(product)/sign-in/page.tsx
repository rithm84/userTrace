import type { Metadata } from "next";
import type { JSX } from "react";

import { StaffSignInForm } from "@/components/auth/StaffSignInForm";
import { ProductShell } from "@/components/product/ProductShell";
import { readNextPath, readReason, type SearchParams } from "@/lib/query";
import { DASHBOARD_PATH, PARTICIPANT_SIGN_IN_PATH } from "@/lib/routes";

export const metadata: Metadata = { title: "Sign in — uTrace" };

export default async function StaffSignInPage(
  props: Readonly<{ searchParams: Promise<SearchParams> }>,
): Promise<JSX.Element> {
  const params = await props.searchParams;

  return (
    <ProductShell main="contained" links={[]} current={null} viewerLabel={null}>
      <div className="pr-stack" style={{ maxWidth: 440 }} data-testid="staff-sign-in">
        <div>
          <h1 className="pr-h1">Sign in to uTrace</h1>
          <p className="pr-lede">
            Client owners and admins sign in with the work email their uTrace membership uses.
          </p>
        </div>
        <StaffSignInForm nextPath={readNextPath(params, DASHBOARD_PATH)} reason={readReason(params)} />
        <p className="pr-note">
          Taking part in an interview instead?{" "}
          <a href={PARTICIPANT_SIGN_IN_PATH} data-testid="staff-sign-in-participant-link">
            Sign in with Discord
          </a>
          .
        </p>
      </div>
    </ProductShell>
  );
}
