import { describe, expect, it, vi } from "vitest";

import { checkMicrophone, identifyBrowser, MINIMUM_CHROME_MAJOR } from "@/lib/session/preflight";

function navigatorWith(
  overrides: Readonly<{ userAgent: string; brands?: readonly { brand: string; version: string }[] }>,
): Navigator {
  return {
    userAgent: overrides.userAgent,
    userAgentData: overrides.brands === undefined ? undefined : { brands: overrides.brands },
  } as unknown as Navigator;
}

const CHROME_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";

describe("participant browser check", () => {
  it("accepts Google Chrome at or above the extension's minimum", () => {
    const identity = identifyBrowser(
      navigatorWith({ userAgent: CHROME_UA, brands: [{ brand: "Google Chrome", version: "141" }] }),
    );

    expect(identity).toEqual({ name: "Google Chrome", majorVersion: 141, supported: true });
    expect(MINIMUM_CHROME_MAJOR).toBe(137);
  });

  it("rejects a Chrome older than the extension supports", () => {
    const identity = identifyBrowser(
      navigatorWith({ userAgent: CHROME_UA, brands: [{ brand: "Google Chrome", version: "115" }] }),
    );

    expect(identity.supported).toBe(false);
  });

  it("rejects other Chromium browsers and non-Chromium browsers by name", () => {
    expect(
      identifyBrowser(navigatorWith({ userAgent: `${CHROME_UA} Edg/141.0.0.0` })),
    ).toEqual({ name: "Microsoft Edge", majorVersion: 0, supported: false });

    expect(
      identifyBrowser(
        navigatorWith({ userAgent: "Mozilla/5.0 (Macintosh) Gecko/20100101 Firefox/131.0" }),
      ),
    ).toEqual({ name: "Firefox", majorVersion: 0, supported: false });
  });

  it("falls back to the user-agent string when the browser reports no brands", () => {
    expect(identifyBrowser(navigatorWith({ userAgent: CHROME_UA }))).toEqual({
      name: "Google Chrome",
      majorVersion: 141,
      supported: true,
    });
  });
});

describe("microphone check", () => {
  it("reports a refusal as an answer rather than a fault", async () => {
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    await expect(checkMicrophone()).resolves.toBe(false);
    vi.unstubAllGlobals();
  });

  it("stops the tracks it opened once permission is proven", async () => {
    const stop = vi.fn();
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }),
      },
    });

    await expect(checkMicrophone()).resolves.toBe(true);
    expect(stop).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
