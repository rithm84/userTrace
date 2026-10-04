import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { JSX } from "react";

import { ProductShell } from "@/components/product/ProductShell";
import { SessionTab } from "@/components/session/SessionTab";
import { isNotFound } from "@/lib/api/errors";
import { requireParticipant } from "@/lib/auth/guards";
import { readPublicEnvironment, usesFixtureControlPlane } from "@/lib/env";
import { sessionPath } from "@/lib/routes";
import { readSessionProjection } from "@/lib/session/projection";

export const metadata: Metadata = { title: "uTrace session" };

/**
 * The session tab. It carries no navigation and no sign-out control, because it
 * is the live session's anchor: navigating it away ends the encounter.
 *
 * The API's durable Interview Session record proves this reader may be here;
 * the committed projection in `public.session_projections` is what the tab
 * renders, and it is the same row the tab then subscribes to over Realtime.
 */
export default async function SessionPage(
  props: Readonly<{ params: Promise<{ sessionId: string }> }>,
): Promise<JSX.Element> {
  const { sessionId } = await props.params;
  const { api } = await requireParticipant(sessionPath(sessionId));

  await api.getSession(sessionId).catch((error: unknown) => {
    if (isNotFound(error)) {
      notFound();
    }
    throw error;
  });

  const read = await readSessionProjection(sessionId);
  if (!read.ok) {
    return (
      <ProductShell main="fill" links={[]} current={null} viewerLabel={null}>
        <div className="ss-single" data-testid="session-tab" data-state="unavailable">
          <div className="ss-card">
            <h1 className="pr-h2">This session is not ready</h1>
            <p className="pr-lede" style={{ textAlign: "center" }} data-testid="session-unavailable">
              {read.detail}
            </p>
          </div>
        </div>
      </ProductShell>
    );
  }

  return (
    <ProductShell main="fill" links={[]} current={null} viewerLabel={null}>
      <SessionTab
        initialProjection={read.projection}
        extensionId={readPublicEnvironment().extensionId}
        voiceTransport={usesFixtureControlPlane() ? "fixture" : "webrtc"}
      />
    </ProductShell>
  );
}
