import "server-only";

import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";

import { readPublicEnvironment } from "@/lib/env";

/**
 * A read-only Supabase client for Server Components.
 *
 * Server Components cannot write response cookies, so `setAll` is omitted, as
 * `@supabase/ssr` prescribes. Token refresh happens once per request in
 * `proxy.ts` before rendering starts; see `lib/supabase/proxy.ts`.
 */
export async function createSupabaseReaderClient(): Promise<SupabaseClient> {
  // Read the cookies first: it is what marks the render dynamic, so an
  // authenticated page is never prerendered and never needs configuration at
  // build time.
  const cookieStore = await cookies();
  const environment = readPublicEnvironment();
  return createServerClient(environment.supabaseUrl, environment.supabasePublishableKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
    },
  });
}

/**
 * A Supabase client for Server Actions, which may write cookies.
 *
 * The cache headers `@supabase/ssr` supplies alongside a cookie write are for
 * cacheable document responses; a Server Action response is a POST that no CDN
 * caches, so they are not applied here.
 */
export async function createSupabaseActionClient(): Promise<SupabaseClient> {
  const cookieStore = await cookies();
  const environment = readPublicEnvironment();
  return createServerClient(environment.supabaseUrl, environment.supabasePublishableKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (cookiesToSet) => {
        for (const { name, value, options } of cookiesToSet) {
          cookieStore.set(name, value, options);
        }
      },
    },
  });
}
