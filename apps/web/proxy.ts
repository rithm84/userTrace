import { NextResponse, type NextRequest } from "next/server";

import { landingOnlyRedirects } from "@/lib/landing-only";
import { isProductPath } from "@/lib/routes";
import { refreshSupabaseSession } from "@/lib/supabase/proxy";

/**
 * The landing-only redirects run first and unchanged. Product requests then get
 * their Supabase session refreshed before rendering, because Server Components
 * cannot write the rotated cookies themselves. The landing needs no
 * authentication configuration, so it never reaches the refresh.
 */
export default async function proxy(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;

  if (landingOnlyRedirects(pathname, process.env)) {
    return NextResponse.redirect(new URL("/", request.url), 307);
  }

  if (!isProductPath(pathname)) {
    return NextResponse.next({ request });
  }

  return refreshSupabaseSession(request);
}

export const config = {
  // Static files and Next.js assets bypass the landing-only gate.
  matcher: ["/((?!_next/|.*\\..*).*)"],
};
