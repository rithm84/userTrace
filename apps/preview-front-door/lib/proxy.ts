import { withoutDoorCookies } from "./session-cookie";
import { routeContextHeader } from "./route-context";
import { UpstreamUnavailableError } from "./errors";
import { productPath } from "./next-assets";

/**
 * The Vercel-to-sandbox request path.
 *
 * The door forwards method, body, and safe headers to the runtime's server-held port URL,
 * strips every caller-supplied provider and route header so no client can forge them,
 * adds its own route context, and streams the runtime's status and body back. It never
 * rewrites a response body. Responses are `no-store` with `Vary: Cookie`, because the
 * same address serves different sessions and CDN caching is prohibited.
 */

/** Headers that belong to one hop and are never forwarded in either direction. */
const hopByHopHeaders: ReadonlySet<string> = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

/** Request headers the door replaces with its own value or drops outright. */
const droppedRequestHeaders: ReadonlySet<string> = new Set([
  "host",
  "cookie",
  "content-length",
  // The door takes identity encoding from the runtime and lets the platform re-encode,
  // so a decoded body can never travel with a stale `content-encoding`.
  "accept-encoding",
  "forwarded",
  "x-real-ip",
]);

/** Caller-supplied prefixes that must never reach the runtime or survive into a response. */
const droppedRequestPrefixes: readonly string[] = [
  "x-utrace-",
  "x-forwarded-",
  "x-vercel-",
];

/** Response headers the door owns or that no longer describe the streamed body. */
const droppedResponseHeaders: ReadonlySet<string> = new Set([
  "content-encoding",
  "content-length",
  "cache-control",
  "vary",
  "set-cookie",
]);

const bodilessMethods: ReadonlySet<string> = new Set(["GET", "HEAD"]);

const isDroppedRequestHeader = (name: string): boolean =>
  hopByHopHeaders.has(name) ||
  droppedRequestHeaders.has(name) ||
  droppedRequestPrefixes.some((prefix) => name.startsWith(prefix));

/** Point the upstream port URL at the requested path and query. */
export function buildUpstreamUrl(params: {
  upstreamBase: URL;
  requestUrl: URL;
}): URL {
  const upstream = new URL(params.upstreamBase.toString());
  upstream.pathname = productPath(params.requestUrl.pathname);
  upstream.search = params.requestUrl.search;
  return upstream;
}

/**
 * The activation-scoped preview entry header.
 *
 * The Server SDK accepts a remote `traceparent` as a parent only on a request
 * that already entered the authenticated, active prepared preview, and this
 * header is that proof. The browser cannot set it — the door does, on every
 * forwarded request — and any caller-supplied `x-utrace-*` is stripped first.
 */
export const previewEntryHeader = "x-utrace-preview-entry";

/** Build the headers forwarded to the preview runtime. */
export function buildUpstreamHeaders(params: {
  requestHeaders: Headers;
  sessionCookieName: string;
  routeContextToken: string;
  previewEntryToken: string;
}): Headers {
  const forwarded = new Headers();
  params.requestHeaders.forEach((value, name) => {
    if (!isDroppedRequestHeader(name)) {
      forwarded.append(name, value);
    }
  });

  const cookieHeader = params.requestHeaders.get("cookie");
  if (cookieHeader !== null) {
    const productCookies = withoutDoorCookies({
      cookieHeader,
      sessionCookieName: params.sessionCookieName,
    });
    if (productCookies !== null) {
      forwarded.set("cookie", productCookies);
    }
  }

  // The session origin travels in the signed route claim: the sandbox port proxy
  // replaces x-forwarded-host with its own host, so the door does not set it.
  forwarded.set("x-forwarded-proto", "https");
  forwarded.set(routeContextHeader, params.routeContextToken);
  forwarded.set(previewEntryHeader, params.previewEntryToken);
  return forwarded;
}

/**
 * Build the headers returned to the browser, keeping the runtime's own cookies and
 * rewriting any redirect that would otherwise hand the browser the provider port URL.
 */
export function buildDownstreamHeaders(params: {
  upstreamHeaders: Headers;
  upstreamOrigin: string;
  publicPreviewHost: string;
}): Headers {
  const downstream = new Headers();
  params.upstreamHeaders.forEach((value, name) => {
    if (hopByHopHeaders.has(name) || droppedResponseHeaders.has(name)) {
      return;
    }
    if (name === "location") {
      downstream.set(
        "location",
        rewriteLocation({
          location: value,
          upstreamOrigin: params.upstreamOrigin,
          publicPreviewHost: params.publicPreviewHost,
        }),
      );
      return;
    }
    downstream.append(name, value);
  });
  for (const cookie of params.upstreamHeaders.getSetCookie()) {
    downstream.append("set-cookie", cookie);
  }
  downstream.set("cache-control", "no-store");
  downstream.set("vary", "Cookie");
  return downstream;
}

/** Replace the provider port origin in a redirect with the public session origin. */
function rewriteLocation(params: {
  location: string;
  upstreamOrigin: string;
  publicPreviewHost: string;
}): string {
  let target: URL;
  try {
    target = new URL(params.location, params.upstreamOrigin);
  } catch {
    return params.location;
  }
  if (target.origin !== params.upstreamOrigin) {
    return params.location;
  }
  return `https://${params.publicPreviewHost}${target.pathname}${target.search}${target.hash}`;
}

/**
 * Forward one request to the preview runtime and stream the answer back.
 *
 * The hop is never retried: the request body is a stream that can be read once, and a
 * repeated product request is a side effect the door has no authority to cause.
 */
export async function forwardToRuntime(params: {
  request: Request;
  upstreamUrl: URL;
  upstreamHeaders: Headers;
  publicPreviewHost: string;
}): Promise<Response> {
  const method = params.request.method.toUpperCase();
  const init: RequestInit & { duplex: "half" } = {
    method,
    headers: params.upstreamHeaders,
    body: bodilessMethods.has(method) ? null : params.request.body,
    duplex: "half",
    redirect: "manual",
    cache: "no-store",
  };

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(params.upstreamUrl, init);
  } catch (cause) {
    throw new UpstreamUnavailableError({
      publicPreviewHost: params.publicPreviewHost,
      cause,
    });
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: buildDownstreamHeaders({
      upstreamHeaders: upstreamResponse.headers,
      upstreamOrigin: params.upstreamUrl.origin,
      publicPreviewHost: params.publicPreviewHost,
    }),
  });
}
