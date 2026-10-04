import {
  handoffCodeSchema,
  productSignedInSchema,
  type PreviewRouteRecord,
  type ProductSignIn,
} from "./contracts";
import type { ControlPlaneOutcome } from "./control-plane";
import {
  buildUpstreamHeaders,
  buildUpstreamUrl,
  forwardToRuntime,
} from "./proxy";
import {
  acceptRouteRevision,
  assertUpstreamBinding,
  isRouteExpired,
  type RouteRefusal,
} from "./route-authority";
import { buildRouteContextClaims, signRouteContext } from "./route-context";
import {
  buildRuntimeCookie,
  buildSessionCookie,
  readSessionCredential,
  readSignedInRuntime,
} from "./session-cookie";
import {
  denialRefusals,
  enterDoor,
  finish,
  interstitialResponse,
  refusalSurfaces,
  unavailable,
  type DoorEntry,
} from "./door-entry";

/**
 * The product request path.
 *
 * A request proves its session with the origin-bound cookie, resolves one committed route
 * from the control plane with caching disabled, passes the door's own fail-closed guards,
 * and is then signed and streamed to the Interview Session's preview runtime. The claim
 * endpoint is here too, because it is what makes that cookie exist, and so is the product
 * sign-in the claim hands the browser on to.
 */

/** Where a claimed browser goes next: the product sign-in, before any product page. */
export const SIGN_IN_PATH = "/__utrace/sign-in";

/** The sign-in query parameter naming the page to return to afterwards. */
const RETURN_TO_PARAMETER = "return_to";

const returnBase = "https://door.invalid";

/** A page load, as opposed to a script's request: only these are sent to sign in again. */
function isPageNavigation(request: Request): boolean {
  return (
    request.method === "GET" && request.headers.get("sec-fetch-mode") === "navigate"
  );
}

/** The same-origin path and query a sign-in may return to, or `null` for anything else. */
function sameOriginReturn(value: string | null): string | null {
  if (value === null || !value.startsWith("/") || value.length > 2048) {
    return null;
  }
  const parsed = new URL(value, returnBase);
  if (parsed.origin !== returnBase || parsed.pathname.startsWith("/__utrace/")) {
    return null;
  }
  return `${parsed.pathname}${parsed.search}`;
}

type RouteOutcome =
  | { readonly kind: "resolved"; readonly record: PreviewRouteRecord }
  | { readonly kind: "refused"; readonly refusal: RouteRefusal };

/** Resolve the committed route for this request and apply the door's own guards. */
async function resolveRoute(params: {
  entry: DoorEntry;
  sessionCredential: string;
  now: Date;
}): Promise<RouteOutcome> {
  const resolution = await params.entry.client.resolveRoute({
    publicPreviewHost: params.entry.origin.publicPreviewHost,
    sessionCredential: params.sessionCredential,
  });
  if (resolution.outcome === "denied") {
    return { kind: "refused", refusal: denialRefusals[resolution.reason] };
  }
  const record = resolution.value;
  if (record.public_preview_host !== params.entry.origin.publicPreviewHost) {
    return { kind: "refused", refusal: "unauthorized_session" };
  }
  if (isRouteExpired({ record, now: params.now })) {
    return { kind: "refused", refusal: "route_expired" };
  }
  if (
    !acceptRouteRevision({
      publicPreviewHost: record.public_preview_host,
      routeRevision: record.route_revision,
    })
  ) {
    return { kind: "refused", refusal: "stale_route_revision" };
  }
  return { kind: "resolved", record };
}

/** Show the participant the surface for one refusal. */
function refused(params: {
  request: Request;
  startedAt: number;
  refusal: RouteRefusal;
}): Response {
  const surface = refusalSurfaces[params.refusal];
  return finish({
    request: params.request,
    startedAt: params.startedAt,
    response: interstitialResponse(surface),
    outcome: surface.kind,
    reason: params.refusal,
    route: null,
  });
}

/** Serve one product request: verify, resolve, sign, forward, and stream the answer. */
export async function handleProxyRequest(request: Request): Promise<Response> {
  const startedAt = Date.now();
  const entered = enterDoor({ request, startedAt, json: false });
  if (entered.kind === "refused") {
    return entered.response;
  }
  const { config, origin } = entered.entry;

  const sessionCredential = readSessionCredential({
    cookieHeader: request.headers.get("cookie"),
    cookieName: config.sessionCookieName,
  });
  if (sessionCredential === null) {
    return finish({
      request,
      startedAt,
      response: interstitialResponse({ kind: "neutral", status: 404 }),
      outcome: "neutral",
      reason: "missing_session_cookie",
      route: null,
    });
  }

  const now = new Date();
  let route: RouteOutcome;
  try {
    route = await resolveRoute({
      entry: entered.entry,
      sessionCredential,
      now,
    });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: "route_resolution_failed",
      status: 502,
      json: false,
      error,
    });
  }

  if (route.kind === "refused") {
    const surface = refusalSurfaces[route.refusal];
    return finish({
      request,
      startedAt,
      response: interstitialResponse(surface),
      outcome: surface.kind,
      reason: route.refusal,
      route: null,
    });
  }

  const record = route.record;
  if (record.readiness_state === "reconnecting") {
    return finish({
      request,
      startedAt,
      response: interstitialResponse({ kind: "reconnecting", status: 503 }),
      outcome: "reconnecting",
      reason: "readiness_withdrawn",
      route: record,
    });
  }

  // The product's session is signed with a secret that belongs to the runtime that
  // issued it, so after the origin advances to a candidate the browser signs in again
  // and comes straight back to the page it asked for.
  if (
    isPageNavigation(request) &&
    readSignedInRuntime({
      cookieHeader: request.headers.get("cookie"),
      sessionCookieName: config.sessionCookieName,
    }) !== record.preview_runtime_allocation_id
  ) {
    const requested = new URL(request.url);
    const location = `${SIGN_IN_PATH}?${new URLSearchParams({
      [RETURN_TO_PARAMETER]: `${requested.pathname}${requested.search}`,
    }).toString()}`;
    return finish({
      request,
      startedAt,
      response: new Response(null, {
        status: 303,
        headers: { location, "cache-control": "no-store", vary: "Cookie" },
      }),
      outcome: "redirected_to_sign_in",
      reason: "product_signed_in_to_another_runtime",
      route: record,
    });
  }

  try {
    const upstreamBase = assertUpstreamBinding({
      record,
      applicationPort: config.upstreamApplicationPort,
    });
    const routeContextToken = signRouteContext({
      claims: buildRouteContextClaims({
        record,
        issuedAt: now,
        ttlSeconds: config.routeContextTtlSeconds,
      }),
      signingSecret: record.route_context_signing_secret,
    });
    const response = await forwardToRuntime({
      request,
      upstreamUrl: buildUpstreamUrl({
        upstreamBase,
        requestUrl: new URL(request.url),
      }),
      upstreamHeaders: buildUpstreamHeaders({
        requestHeaders: request.headers,
        sessionCookieName: config.sessionCookieName,
        routeContextToken,
        previewEntryToken: record.preview_entry_token,
      }),
      publicPreviewHost: origin.publicPreviewHost,
    });
    return finish({
      request,
      startedAt,
      response,
      outcome: "proxied",
      reason: null,
      route: record,
    });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: "runtime_hop_failed",
      status: 502,
      json: false,
      error,
    });
  }
}

/** Exchange a one-time handoff code for the origin-bound session cookie. */
export async function handleClaimRequest(request: Request): Promise<Response> {
  const startedAt = Date.now();
  const entered = enterDoor({ request, startedAt, json: false });
  if (entered.kind === "refused") {
    return entered.response;
  }
  const { config, client, origin } = entered.entry;

  const code = handoffCodeSchema.safeParse(
    new URL(request.url).searchParams.get("code"),
  );
  if (!code.success) {
    return finish({
      request,
      startedAt,
      response: interstitialResponse({ kind: "neutral", status: 404 }),
      outcome: "neutral",
      reason: "unusable_handoff_code",
      route: null,
    });
  }

  let exchanged;
  try {
    exchanged = await client.exchangeHandoff({
      code: code.data,
      publicPreviewHost: origin.publicPreviewHost,
    });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: "handoff_exchange_failed",
      status: 502,
      json: false,
      error,
    });
  }

  if (exchanged.outcome === "denied") {
    const surface = refusalSurfaces[denialRefusals[exchanged.reason]];
    return finish({
      request,
      startedAt,
      response: interstitialResponse(surface),
      outcome: surface.kind,
      reason: exchanged.reason,
      route: null,
    });
  }

  const exchange = exchanged.value;
  const remainingSeconds = Math.floor(
    (new Date(exchange.session_credential.expires_at).getTime() - startedAt) /
      1_000,
  );
  if (
    exchange.public_preview_host !== origin.publicPreviewHost ||
    remainingSeconds <= 0
  ) {
    return finish({
      request,
      startedAt,
      response: interstitialResponse({ kind: "neutral", status: 404 }),
      outcome: "neutral",
      reason:
        exchange.public_preview_host === origin.publicPreviewHost
          ? "session_credential_expired"
          : "exchange_host_mismatch",
      route: null,
    });
  }

  const response = new Response(null, {
    status: 303,
    headers: {
      location: SIGN_IN_PATH,
      "cache-control": "no-store",
      vary: "Cookie",
      "set-cookie": buildSessionCookie({
        cookieName: config.sessionCookieName,
        sessionCredential: exchange.session_credential.value,
        maxAgeSeconds: Math.min(
          config.sessionCookieMaxAgeSeconds,
          remainingSeconds,
        ),
      }),
    },
  });
  return finish({
    request,
    startedAt,
    response,
    outcome: "claimed",
    reason: null,
    route: null,
  });
}


/**
 * Sign the claimed browser into the client product.
 *
 * The claim admits the browser to the session origin, and the product still has its own
 * sign-in. This step gets a one-time token from the control plane, posts it server-side to
 * the sign-in path the pinned template declares, and hands the browser the product's own
 * session cookie. The token never reaches the browser, and a runtime that does not answer
 * with a signed-in session fails the step rather than leaving the participant at a login form.
 */
export async function handleSignInRequest(request: Request): Promise<Response> {
  const startedAt = Date.now();
  const entered = enterDoor({ request, startedAt, json: false });
  if (entered.kind === "refused") {
    return entered.response;
  }
  const { config, client, origin } = entered.entry;

  const sessionCredential = readSessionCredential({
    cookieHeader: request.headers.get("cookie"),
    cookieName: config.sessionCookieName,
  });
  if (sessionCredential === null) {
    return finish({
      request,
      startedAt,
      response: interstitialResponse({ kind: "neutral", status: 404 }),
      outcome: "neutral",
      reason: "missing_session_cookie",
      route: null,
    });
  }

  const now = new Date();
  let route: RouteOutcome;
  try {
    route = await resolveRoute({ entry: entered.entry, sessionCredential, now });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: "route_resolution_failed",
      status: 502,
      json: false,
      error,
    });
  }
  if (route.kind === "refused") {
    return refused({ request, startedAt, refusal: route.refusal });
  }
  const record = route.record;
  if (record.readiness_state === "reconnecting") {
    return finish({
      request,
      startedAt,
      response: interstitialResponse({ kind: "reconnecting", status: 503 }),
      outcome: "reconnecting",
      reason: "readiness_withdrawn",
      route: record,
    });
  }

  let minted: ControlPlaneOutcome<ProductSignIn>;
  try {
    minted = await client.mintProductSignIn({
      publicPreviewHost: origin.publicPreviewHost,
      sessionCredential,
    });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: "product_sign_in_mint_failed",
      status: 502,
      json: false,
      error,
    });
  }
  if (minted.outcome === "denied") {
    return refused({ request, startedAt, refusal: denialRefusals[minted.reason] });
  }

  let answer: Response;
  try {
    const upstreamUrl = buildUpstreamUrl({
      upstreamBase: assertUpstreamBinding({
        record,
        applicationPort: config.upstreamApplicationPort,
      }),
      requestUrl: new URL(minted.value.path, request.url),
    });
    answer = await fetch(upstreamUrl, {
      method: "POST",
      headers: buildUpstreamHeaders({
        requestHeaders: new Headers({ "content-type": "application/json" }),
        sessionCookieName: config.sessionCookieName,
        routeContextToken: signRouteContext({
          claims: buildRouteContextClaims({
            record,
            issuedAt: now,
            ttlSeconds: config.routeContextTtlSeconds,
          }),
          signingSecret: record.route_context_signing_secret,
        }),
        previewEntryToken: record.preview_entry_token,
      }),
      body: JSON.stringify({ token: minted.value.token }),
      redirect: "manual",
      cache: "no-store",
    });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: "runtime_hop_failed",
      status: 502,
      json: false,
      error,
    });
  }

  const signedIn = productSignedInSchema.safeParse(
    answer.status === 200 ? await answer.json().catch(() => null) : null,
  );
  const cookies = answer.headers.getSetCookie();
  if (!signedIn.success || cookies.length === 0) {
    return unavailable({
      request,
      startedAt,
      reason: "product_sign_in_refused",
      status: 502,
      json: false,
      error: new Error(
        `the product answered its sign-in with HTTP ${answer.status} and ${cookies.length} cookies instead of a signed-in session`,
      ),
    });
  }

  const headers = new Headers({
    location:
      sameOriginReturn(new URL(request.url).searchParams.get(RETURN_TO_PARAMETER)) ??
      signedIn.data.redirect_to,
    "cache-control": "no-store",
    vary: "Cookie",
  });
  for (const cookie of cookies) {
    headers.append("set-cookie", cookie);
  }
  headers.append(
    "set-cookie",
    buildRuntimeCookie({
      sessionCookieName: config.sessionCookieName,
      previewRuntimeAllocationId: record.preview_runtime_allocation_id,
      maxAgeSeconds: config.sessionCookieMaxAgeSeconds,
    }),
  );
  return finish({
    request,
    startedAt,
    response: new Response(null, { status: 303, headers }),
    outcome: "signed_in",
    reason: null,
    route: record,
  });
}
