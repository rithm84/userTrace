import "server-only";

import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { readPublicEnvironment } from "@/lib/env";

/**
 * Refreshes the Supabase session for one product request.
 *
 * Server Components cannot write cookies, so the refreshed tokens are written
 * here: onto the forwarded request (so the render reads the new session) and
 * onto the response (so the browser stores it). `getClaims` verifies the JWT
 * signature rather than trusting the cookie, which is what makes the refreshed
 * session safe to act on.
 */
export async function refreshSupabaseSession(request: NextRequest): Promise<NextResponse> {
  const environment = readPublicEnvironment();
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    environment.supabaseUrl,
    environment.supabasePublishableKey,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (cookiesToSet, headers) => {
          for (const { name, value } of cookiesToSet) {
            request.cookies.set(name, value);
          }
          response = NextResponse.next({ request });
          for (const { name, value, options } of cookiesToSet) {
            response.cookies.set(name, value, options);
          }
          for (const [header, value] of Object.entries(headers)) {
            response.headers.set(header, value);
          }
        },
      },
    },
  );

  await supabase.auth.getClaims();

  return response;
}
