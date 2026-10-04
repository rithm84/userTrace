import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy } from "@/lib/csp";

describe("product Content-Security-Policy", () => {
  it("admits the configured Supabase project over HTTPS and WSS, and nothing else", () => {
    const csp = buildContentSecurityPolicy({
      NODE_ENV: "production",
      NEXT_PUBLIC_SUPABASE_URL: "https://project-ref.supabase.co",
    });

    expect(csp).toContain(
      "connect-src 'self' https://project-ref.supabase.co wss://project-ref.supabase.co;",
    );
    expect(csp).toContain("media-src 'self' blob: mediastream:");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("api.utrace.dev");
  });

  it("refuses a Supabase URL that is not an absolute origin", () => {
    expect(() =>
      buildContentSecurityPolicy({ NODE_ENV: "production", NEXT_PUBLIC_SUPABASE_URL: "project-ref" }),
    ).toThrow(TypeError);
  });
});
