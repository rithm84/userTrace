import "server-only";

import { headers } from "next/headers";

import { ConfigurationError } from "@/lib/env";

/**
 * The origin this request arrived on, used to build the absolute redirect URL
 * Supabase sends the browser back to. Taken from the forwarded headers so a
 * preview deployment and a local dev server both work without configuration.
 */
export async function requestOrigin(): Promise<string> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host");
  if (host === null || host.length === 0) {
    throw new ConfigurationError(
      "missing_setting",
      "host",
      "The request carried no Host header, so the sign-in redirect URL cannot be built.",
    );
  }
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") || host.startsWith("127.0.0.1") ? "http" : "https");
  return `${protocol}://${host}`;
}
