import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ExtensionLinkPage from "@/app/extension/link/page";
import { ControlPlaneError } from "@/lib/api/errors";
import { FixtureControlPlaneClient } from "@/lib/api/fixture";

/**
 * The one-time extension account link.
 *
 * The extension opens this page inside `chrome.identity.launchWebAuthFlow` and
 * expects a redirect to its own `chromiumapp.org` address carrying a
 * `link_code`. Everything else it may be asked to redirect to is refused, so
 * the page cannot be used as an open redirect by a link someone was sent.
 */

const EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop";
const REDIRECT_URI = `https://${EXTENSION_ID}.chromiumapp.org/extension-link`;

const redirect = vi.fn((destination: string) => {
  throw new RedirectSignal(destination);
});

class RedirectSignal extends Error {
  readonly destination: string;

  constructor(destination: string) {
    super(`redirect to ${destination}`);
    this.destination = destination;
  }
}

vi.mock("next/navigation", () => ({
  redirect: (destination: string) => redirect(destination),
  notFound: () => {
    throw new Error("not found");
  },
}));

beforeEach(() => {
  vi.stubEnv("UTRACE_CONTROL_PLANE_FIXTURES", "1");
  vi.stubEnv("UTRACE_PARTICIPANT_FIXTURE", "1");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://project.supabase.co");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "publishable");
  vi.stubEnv("NEXT_PUBLIC_UTRACE_EXTENSION_ID", EXTENSION_ID);
  redirect.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

const open = async (
  searchParams: Record<string, string>,
): Promise<{ redirected: string | null; element: React.ReactElement | null }> => {
  try {
    const element = await ExtensionLinkPage({ searchParams: Promise.resolve(searchParams) });
    return { redirected: null, element };
  } catch (error) {
    if (error instanceof RedirectSignal) {
      return { redirected: error.destination, element: null };
    }
    throw error;
  }
};

describe("the extension account link", () => {
  it("returns a link code on the extension's own redirect address", async () => {
    const result = await open({
      redirect_uri: REDIRECT_URI,
      extension_version: "0.1.0",
      protocol_version: "1.0",
    });

    expect(result.redirected).not.toBeNull();
    const destination = new URL(result.redirected ?? "");
    expect(destination.origin).toBe(`https://${EXTENSION_ID}.chromiumapp.org`);
    expect(destination.searchParams.get("link_code")).toMatch(/^[A-Za-z0-9_-]{16,}$/u);
    // The control plane binds the code to the redirect URI it was minted for,
    // so the page sends what the code carries, not what the query asked for.
    expect(destination.pathname).toBe(new URL(REDIRECT_URI).pathname);
  });

  it.each([
    ["another extension", `https://someoneelse.chromiumapp.org/extension-link`],
    ["an arbitrary site", "https://attacker.example/collect"],
    ["a plain-HTTP address", `http://${EXTENSION_ID}.chromiumapp.org/extension-link`],
    ["an address carrying its own query", `${REDIRECT_URI}?next=https://attacker.example`],
  ])("refuses %s rather than redirecting to it", async (_case, redirectUri) => {
    const result = await open({
      redirect_uri: redirectUri,
      extension_version: "0.1.0",
      protocol_version: "1.0",
    });

    expect(result.redirected).toBeNull();
    expect(redirect).not.toHaveBeenCalled();
    render(result.element ?? <span />);
    expect(screen.getByTestId("extension-link-refused")).toBeTruthy();
  });

  it("refuses an extension protocol this deployment does not speak", async () => {
    const result = await open({
      redirect_uri: REDIRECT_URI,
      extension_version: "0.1.0",
      protocol_version: "2.0",
    });

    expect(result.redirected).toBeNull();
    render(result.element ?? <span />);
    expect(screen.getByTestId("extension-link-reason").textContent).toContain("2.0");
  });

  it("refuses a link that carries none of the extension's parameters", async () => {
    const result = await open({});

    expect(result.redirected).toBeNull();
    render(result.element ?? <span />);
    expect(screen.getByTestId("extension-link-refused")).toBeTruthy();
  });

  it("explains that an invitation comes first when uTrace has no participant to link", async () => {
    const forbidden = vi
      .spyOn(FixtureControlPlaneClient.prototype, "createExtensionLinkCode")
      .mockRejectedValue(
        new ControlPlaneError(
          {
            code: "forbidden",
            method: "POST",
            path: "/v1/extension/link-codes",
            status: 403,
            responseBody: null,
          },
          "refused",
          {},
        ),
      );

    const result = await open({
      redirect_uri: REDIRECT_URI,
      extension_version: "0.1.0",
      protocol_version: "1.0",
    });

    expect(result.redirected).toBeNull();
    render(result.element ?? <span />);
    expect(screen.getByTestId("extension-link-reason").textContent).toContain("invitation");
    forbidden.mockRestore();
  });
});
