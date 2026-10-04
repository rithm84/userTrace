import "server-only";

import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { NextRequest, NextResponse } from "next/server";

import { readPublicEnvironment } from "@/lib/env";

/**
 * A Supabase client for Route Handlers. Cookie writes and the cache headers
 * that must accompany them land on the response the handler returns, so an
 * auth cookie is never cached by a CDN or reverse proxy.
 */
export function createSupabaseRouteClient(
  request: NextRequest,
  response: NextResponse,
): SupabaseClient {
  const environment = readPublicEnvironment();
  return createServerClient(environment.supabaseUrl, environment.supabasePublishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet, headers) => {
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
        for (const [header, value] of Object.entries(headers)) {
          response.headers.set(header, value);
        }
      },
    },
  });
}
