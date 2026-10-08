import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy } from "@/lib/csp";

describe("product Content-Security-Policy", () => {
  it("admits the configured Supabase project and Daily's call domains, and nothing else", () => {
    const csp = buildContentSecurityPolicy({
      NODE_ENV: "production",
      NEXT_PUBLIC_SUPABASE_URL: "https://project-ref.supabase.co",
    });

    expect(csp).toContain(
      "connect-src 'self' https://project-ref.supabase.co wss://project-ref.supabase.co " +
        "https://*.daily.co https://*.dailywebrtc.com https://*.dailywebrtc.net https://prod-ks.pluot.blue " +
        "wss://*.daily.co wss://*.dailywebrtc.com wss://*.dailywebrtc.net;",
    );
    expect(csp).toContain(
      "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://*.daily.co https://*.dailywebrtc.com https://*.dailywebrtc.net;",
    );
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toContain(" wss:;");
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
