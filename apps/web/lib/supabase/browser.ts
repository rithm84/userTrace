"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { readPublicEnvironment } from "@/lib/env";

/**
 * The browser Supabase client. `@supabase/ssr` keeps one instance per page and
 * stores the session in cookies so the server can read the same session.
 * It is used for interactive sign-in and for the session tab's Realtime
 * subscription; every product mutation goes through a Server Action instead.
 */
export function createSupabaseBrowserClient(): SupabaseClient {
  const environment = readPublicEnvironment();
  return createBrowserClient(environment.supabaseUrl, environment.supabasePublishableKey);
}
