/**
 * Session origin parsing.
 *
 * Each Interview Session receives one opaque single-level subdomain of the uTrace-owned
 * preview apex, so its origin is `<opaque>.preview.utrace.dev`. The apex itself, a
 * deeper name, and any unknown host carry no session and get the neutral interstitial.
 */

export interface SessionOrigin {
  /** The opaque single label that identifies the session origin. */
  readonly opaqueLabel: string;
  /** The full lowercase host, without port, used in route lookups and route context. */
  readonly publicPreviewHost: string;
}

const opaqueLabelPattern = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;
const minimumOpaqueLabelLength = 8;
const maximumOpaqueLabelLength = 63;

/** Drop the port and normalise case. Returns `null` for a syntactically unusable host. */
function normaliseHost(hostHeader: string): string | null {
  const trimmed = hostHeader.trim().toLowerCase();
  if (trimmed.length === 0 || trimmed.includes("[") || trimmed.includes("/")) {
    return null;
  }
  const colonIndex = trimmed.indexOf(":");
  const host = colonIndex === -1 ? trimmed : trimmed.slice(0, colonIndex);
  return host.length === 0 ? null : host;
}

/**
 * Resolve the session origin a request arrived on, or `null` when the host is the preview
 * apex, a deeper subdomain, or anything else this door does not serve.
 */
export function readSessionOrigin(params: {
  hostHeader: string | null;
  previewApex: string;
}): SessionOrigin | null {
  if (params.hostHeader === null) {
    return null;
  }
  const host = normaliseHost(params.hostHeader);
  if (host === null) {
    return null;
  }
  const suffix = `.${params.previewApex}`;
  if (!host.endsWith(suffix)) {
    return null;
  }
  const opaqueLabel = host.slice(0, host.length - suffix.length);
  if (
    opaqueLabel.length < minimumOpaqueLabelLength ||
    opaqueLabel.length > maximumOpaqueLabelLength ||
    !opaqueLabelPattern.test(opaqueLabel)
  ) {
    return null;
  }
  return { opaqueLabel, publicPreviewHost: host };
}
