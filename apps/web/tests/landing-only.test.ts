import { describe, expect, it } from "vitest";

import { isLandingOnly, landingOnlyRedirects } from "@/lib/landing-only";

describe("the landing-only gate", () => {
  it("is off unless LANDING_ONLY is exactly 1", () => {
    expect(isLandingOnly({})).toBe(false);
    expect(isLandingOnly({ LANDING_ONLY: "true" })).toBe(false);
    expect(isLandingOnly({ LANDING_ONLY: "0" })).toBe(false);
    expect(isLandingOnly({ LANDING_ONLY: "1" })).toBe(true);
    expect(isLandingOnly({ LANDING_ONLY: " 1 " })).toBe(true);
  });

  it("sends every path but the landing back to it while on, and nothing while off", () => {
    const on = { LANDING_ONLY: "1" };
    expect(landingOnlyRedirects("/", on)).toBe(false);
    for (const path of ["/case-files", "/case-files/abc", "/dashboard", "/teams", "/api/auth/workos/login", "/sessions/1", "/nowhere"]) {
      expect(landingOnlyRedirects(path, on)).toBe(true);
      expect(landingOnlyRedirects(path, {})).toBe(false);
    }
  });
});
