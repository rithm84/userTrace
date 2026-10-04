import type { Metadata } from "next";
import { redirect } from "next/navigation";
import type { JSX } from "react";

import { ProductShell } from "@/components/product/ProductShell";
import { ControlPlaneError } from "@/lib/api/errors";
import { requireParticipant } from "@/lib/auth/guards";
import { readPublicEnvironment } from "@/lib/env";
import { EXTENSION_LINK_PATH } from "@/lib/routes";

export const metadata: Metadata = { title: "Link the uTrace extension" };

/** `chrome.identity.launchWebAuthFlow` only ever returns to this host. */
const REDIRECT_HOST_SUFFIX = ".chromiumapp.org";
const EXTENSION_VERSION_PATTERN = /^[0-9]+(\.[0-9]+){0,3}$/u;
const PROTOCOL_VERSION_PATTERN = /^1\.[0-9]{1,3}$/u;

type LinkQuery = Readonly<{
  redirectUri: string;
  extensionVersion: string;
  protocolVersion: string;
}>;

type Refusal = Readonly<{ reason: string }>;

/**
 * The one-time extension account link.
 *
 * The extension opens this page inside `chrome.identity.launchWebAuthFlow`,
 * authenticated as the uTrace User. uTrace mints a single-use link code bound
 * to that person and to the extension's own `chromiumapp.org` redirect URI, and
 * this page redirects there with `link_code` in the query; the extension then
 * exchanges it at `POST /v1/extension/installations` together with a device
 * public key and a proof of possession.
 *
 * The page hands out nothing else. It never sees an installation identity, a
 * device key, a control-plane credential, or an interview, and it refuses any
 * `redirect_uri` that is not this deployment's own extension, so it cannot be
 * used as an open redirect.
 */
export default async function ExtensionLinkPage(
  props: Readonly<{ searchParams: Promise<Record<string, string | string[] | undefined>> }>,
): Promise<JSX.Element> {
  const searchParams = await props.searchParams;
  const { api } = await requireParticipant(nextPath(searchParams));

  const query = readQuery(searchParams, readPublicEnvironment().extensionId);
  if ("reason" in query) {
    return refused(query.reason);
  }

  let minted: Awaited<ReturnType<typeof api.createExtensionLinkCode>>;
  try {
    minted = await api.createExtensionLinkCode({
      redirect_uri: query.redirectUri,
      extension_version: query.extensionVersion,
      protocol_version: query.protocolVersion,
    });
  } catch (error) {
    // A signed-in person becomes a uTrace participant by claiming an invitation,
    // so before that claim the control plane has no one to link the extension to.
    if (error instanceof ControlPlaneError && error.code === "forbidden") {
      return refused(
        "Open the interview invitation uTrace sent you on Discord first, then link this browser from your session tab.",
      );
    }
    throw error;
  }

  // The control plane binds the code to the redirect URI it was minted for, so
  // the redirect uses the URI it returns rather than the one this page read.
  if (minted.redirect_uri !== query.redirectUri) {
    return refused(
      "uTrace minted this link code for a different redirect address, so it will not be sent.",
    );
  }
  const destination = new URL(minted.redirect_uri);
  destination.searchParams.set("link_code", minted.link_code);
  redirect(destination.toString());
}

/** The path to return to after sign-in, with the extension's own query intact. */
function nextPath(searchParams: Record<string, string | string[] | undefined>): string {
  const carried = new URLSearchParams();
  for (const key of ["redirect_uri", "extension_version", "protocol_version"]) {
    const value = single(searchParams[key]);
    if (value !== null) {
      carried.set(key, value);
    }
  }
  const query = carried.toString();
  return query.length === 0 ? EXTENSION_LINK_PATH : `${EXTENSION_LINK_PATH}?${query}`;
}

function single(value: string | string[] | undefined): string | null {
  if (typeof value === "string" && value.length > 0) {
    return value;
  }
  return null;
}

/**
 * Validates the extension's three parameters. The redirect URI must be this
 * deployment's own extension: a different extension, a different scheme, or a
 * URI carrying its own query is refused rather than redirected to.
 */
function readQuery(
  searchParams: Record<string, string | string[] | undefined>,
  extensionId: string,
): LinkQuery | Refusal {
  const redirectUri = single(searchParams["redirect_uri"]);
  const extensionVersion = single(searchParams["extension_version"]);
  const protocolVersion = single(searchParams["protocol_version"]);

  if (redirectUri === null || extensionVersion === null || protocolVersion === null) {
    return { reason: "This link is missing the parameters the uTrace extension sends." };
  }
  if (!EXTENSION_VERSION_PATTERN.test(extensionVersion)) {
    return { reason: `"${extensionVersion}" is not a uTrace extension version.` };
  }
  if (!PROTOCOL_VERSION_PATTERN.test(protocolVersion)) {
    return {
      reason: `This uTrace deployment does not speak extension protocol ${protocolVersion}.`,
    };
  }

  let parsed: URL;
  try {
    parsed = new URL(redirectUri);
  } catch {
    return { reason: "The extension sent a redirect address uTrace cannot read." };
  }
  if (parsed.protocol !== "https:" || !parsed.host.endsWith(REDIRECT_HOST_SUFFIX)) {
    return { reason: "uTrace only returns a link code to a Chrome extension redirect address." };
  }
  if (parsed.host !== `${extensionId}${REDIRECT_HOST_SUFFIX}`) {
    return { reason: "That redirect address belongs to another extension, not the uTrace one." };
  }
  if (parsed.search.length > 0 || parsed.hash.length > 0) {
    return { reason: "The extension's redirect address must carry no query or fragment." };
  }

  return { redirectUri: parsed.toString(), extensionVersion, protocolVersion };
}

function refused(reason: string): JSX.Element {
  return (
    <ProductShell main="contained" links={[]} current={null} viewerLabel={null}>
      <section className="pr-panel" data-testid="extension-link-refused">
        <h1 className="pr-h2">uTrace cannot link this extension</h1>
        <p className="pr-message" role="alert" data-testid="extension-link-reason">
          {reason}
        </p>
        <p className="pr-note">
          Open the uTrace extension and choose <strong>Link account</strong> again. If this keeps
          happening, reinstall it from the Chrome Web Store.
        </p>
      </section>
    </ProductShell>
  );
}
