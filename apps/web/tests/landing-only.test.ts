import { describe, expect, it } from "vitest";

import { isLandingOnly } from "@/lib/landing-only";

describe("the landing-only gate", () => {
  it("is off unless LANDING_ONLY is exactly 1", () => {
    expect(isLandingOnly({})).toBe(false);
    expect(isLandingOnly({ LANDING_ONLY: "true" })).toBe(false);
    expect(isLandingOnly({ LANDING_ONLY: "0" })).toBe(false);
    expect(isLandingOnly({ LANDING_ONLY: "1" })).toBe(true);
    expect(isLandingOnly({ LANDING_ONLY: " 1 " })).toBe(true);
  });
});
