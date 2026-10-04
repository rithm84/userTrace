import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { PARTICIPANT_SIGN_IN_PATH, STAFF_SIGN_IN_PATH, USER_SETTINGS_PATH } from "@/lib/routes";
import { createSupabaseRouteClient } from "@/lib/supabase/route";

/** Only in-application destinations are followed after sign-in. */
const nextPathSchema = z.string().regex(/^\/(?!\/)[A-Za-z0-9\-._~!$&'()*+,;=:@/%]*$/u);
const emailOtpTypeSchema = z.enum(["magiclink", "email", "invite", "signup", "recovery", "email_change"]);

/**
 * The single sign-in callback.
 *
 * Discord returns a PKCE `code`; an emailed staff link returns a `token_hash`.
 * Both establish the cookie session on the response this handler returns, which
 * also carries the no-store headers `@supabase/ssr` requires for a cookie write.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const parameters = request.nextUrl.searchParams;
  const nextPath = nextPathSchema.safeParse(parameters.get("next"));
  const destination = nextPath.success ? nextPath.data : USER_SETTINGS_PATH;

  const providerError = parameters.get("error_description") ?? parameters.get("error");
  if (providerError !== null) {
    return NextResponse.redirect(signInFailure(request, destination, providerError));
  }

  const response = NextResponse.redirect(new URL(destination, request.url));
  const supabase = createSupabaseRouteClient(request, response);

  const code = parameters.get("code");
  if (code !== null) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error !== null) {
      return NextResponse.redirect(signInFailure(request, destination, error.message));
    }
    return response;
  }

  const tokenHash = parameters.get("token_hash");
  const otpType = emailOtpTypeSchema.safeParse(parameters.get("type"));
  if (tokenHash !== null && otpType.success) {
    const { error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type: otpType.data });
    if (error !== null) {
      return NextResponse.redirect(signInFailure(request, destination, error.message));
    }
    return response;
  }

  return NextResponse.redirect(
    signInFailure(request, destination, "The sign-in link carried no authorization code."),
  );
}

function signInFailure(request: NextRequest, destination: string, reason: string): URL {
  const route = destination.startsWith("/dashboard") ? STAFF_SIGN_IN_PATH : PARTICIPANT_SIGN_IN_PATH;
  const url = new URL(route, request.url);
  url.searchParams.set("next", destination);
  url.searchParams.set("reason", reason);
  return url;
}
