import { afterEach, describe, expect, it, vi } from "vitest";
import { NextResponse, NextRequest } from "next/server";

import proxy from "../proxy";

const refreshSupabaseSession = vi.hoisted(() => vi.fn());

vi.mock("@/lib/supabase/proxy", () => ({ refreshSupabaseSession }));

afterEach(() => {
  vi.unstubAllEnvs();
  refreshSupabaseSession.mockReset();
});

describe("landing request proxy", () => {
  it("redirects former product paths to the landing when LANDING_ONLY=1", async () => {
    vi.stubEnv("LANDING_ONLY", "1");
    for (const path of ["/dashboard", "/case-files/abc", "/teams", "/api/auth/workos/login", "/sessions/1"]) {
      const response = await proxy(new NextRequest(`https://utrace.dev${path}`));
      expect(response.status).toBe(307);
      expect(response.headers.get("location")).toBe("https://utrace.dev/");
    }
    const landing = await proxy(new NextRequest("https://utrace.dev/"));
    expect(landing.headers.get("location")).toBeNull();
    expect(refreshSupabaseSession).not.toHaveBeenCalled();
  });

  it("leaves missing-route handling to Next.js when the gate is off", async () => {
    vi.stubEnv("LANDING_ONLY", "0");
    for (const path of ["/", "/teams", "/api/auth/workos/login"]) {
      const response = await proxy(new NextRequest(`https://utrace.dev${path}`));
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("x-middleware-next")).toBe("1");
    }
    expect(refreshSupabaseSession).not.toHaveBeenCalled();
  });
});

describe("product session refresh", () => {
  it("refreshes the Supabase session on product paths only", async () => {
    vi.stubEnv("LANDING_ONLY", "0");
    refreshSupabaseSession.mockImplementation((request: NextRequest) =>
      Promise.resolve(NextResponse.next({ request })),
    );

    for (const path of ["/dashboard", "/dashboard/settings", "/case-files/cf-1", "/sessions/s-1", "/invitations/t", "/user-settings", "/sign-in", "/auth/callback"]) {
      await proxy(new NextRequest(`https://utrace.dev${path}`));
    }

    expect(refreshSupabaseSession).toHaveBeenCalledTimes(8);
  });
});
