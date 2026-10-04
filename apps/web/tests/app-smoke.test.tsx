import { render, screen, within } from "@testing-library/react";
import type { AgentCharacterProps } from "@utrace/agent-character";
import { createElement } from "react";
import { vi } from "vitest";
import ErrorPage from "../app/error";
import NotFound from "../app/not-found";
import HomePage, { metadata as homeMetadata } from "../app/page";

vi.mock("@utrace/agent-character", () => ({
  acceptCharacterProjection: (
    _previous: AgentCharacterProps["projection"] | null,
    candidate: AgentCharacterProps["projection"],
  ) => ({ kind: "applied", projection: Object.freeze(candidate) }),
  createAgentCharacterResources: (assetUrl: string, wasmUrl: string) =>
    Object.freeze({ assetUrl, wasmUrl }),
  configureAgentCharacterRuntime: () => undefined,
  AgentCharacter: (props: AgentCharacterProps) =>
    createElement("div", { "data-testid": props.dataTestId, "data-state": props.projection.state }),
}));

// the landing rigs talk to the Rive runtime directly; jsdom has no WebGL
vi.mock("@rive-app/react-webgl2", () => ({
  useRive: () => ({ rive: null, RiveComponent: () => createElement("canvas") }),
  useViewModelInstanceNumber: () => ({ value: 0, setValue: () => undefined }),
  useViewModelInstanceEnum: () => ({ value: "idle", values: [], setValue: () => undefined }),
}));
vi.mock("@rive-app/webgl2", () => ({
  Alignment: { Center: "center" },
  Fit: { Contain: "contain" },
  Layout: class Layout {},
}));

describe("web route smoke", () => {
  it("renders the landing: nav, hero, the seven sections and the footer", () => {
    render(<HomePage />);

    expect(screen.getByRole("heading", { level: 1, name: /ship .* your customers actually want/i })).toBeInTheDocument();
    expect(screen.getByTestId("hero-waitlist-email")).toHaveAttribute("type", "email");
    expect(screen.getByTestId("hero-waitlist-email")).toBeRequired();
    const nav = within(screen.getByTestId("landing-navbar"));
    const primary = within(nav.getByRole("navigation", { name: "Primary" }));
    expect(primary.getByRole("link", { name: "How it works" })).toHaveAttribute("href", "#how-it-works");
    expect(primary.getByRole("link", { name: "Where it helps" })).toHaveAttribute("href", "#where-it-helps");
    expect(primary.getByRole("link", { name: "For your team" })).toHaveAttribute("href", "#what-you-get");
    expect(primary.getByRole("link", { name: "For your users" })).toHaveAttribute("href", "#for-your-users");
    expect(primary.getByRole("link", { name: "How it fits" })).toHaveAttribute("href", "#integrations");
    expect(primary.queryByRole("link", { name: "Docs" })).toBeNull();
    expect(screen.getByTestId("nav-waitlist")).toHaveAttribute("href", "#waitlist");
    expect(screen.getByTestId("nav-book-demo")).toHaveAttribute("href", "mailto:rithm84@utrace.dev");
    for (const id of ["landing-journey", "landing-helps", "landing-casefile", "landing-users", "landing-fits", "landing-closing", "landing-footer"]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    expect(screen.getByTestId("hero-cta-row")).toBeInTheDocument();
    // The hero paints before measurement; the canvas waits for an actual layout box.
    expect(screen.getByTestId("landing-journey")).toHaveAttribute("id", "landing-journey");
    expect(screen.getByTestId("journey-stage")).toContainElement(screen.getByTestId("landing-hero"));
    expect(screen.getByTestId("landing-hero")).toContainElement(screen.getByTestId("hero-city"));
    expect(screen.getByTestId("landing-casefile")).toHaveAttribute("id", "what-you-get");
    expect(screen.getByTestId("landing-users")).toHaveAttribute("id", "for-your-users");
    // the rewards bay points back to the section that explains them
    expect(screen.getByTestId("fits-rewards-back")).toHaveAttribute("href", "#for-your-users");
    expect(screen.getByTestId("landing-closing")).toHaveAttribute("id", "book-demo");
    // the closing arrives mid tug of war: every rig strains, the glass is off its spot, the buttons wait
    expect(screen.getByTestId("landing-closing")).toHaveAttribute("data-phase", "pulled");
    expect(screen.getByTestId("closing-coral")).toHaveAttribute("data-state", "concern");
    for (const colour of ["black", "mint", "sky", "lilac", "peach", "aqua"]) {
      expect(screen.getByTestId(`closing-cast-${colour}`)).toHaveAttribute("data-state", "concern");
    }
    expect(screen.getByRole("button", { name: "Pull the magnifying glass into place" })).toBeInTheDocument();
    expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "false");
    expect(screen.getByTestId("closing-lens-copy").textContent).toBe(screen.getByRole("heading", { level: 2, name: /give good/i }).textContent);
    expect(screen.getByTestId("closing-waitlist-email")).toHaveAttribute("type", "email");
    expect(screen.getByTestId("closing-actions")).toHaveAttribute("inert");
    expect(screen.getByTestId("footer-back-to-top")).toBeInTheDocument();
    expect(screen.queryByTestId("edge-mascot-rig")).toBeNull();
  });

  it("renders repo-owned error surfaces", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(<ErrorPage error={new Error("boom")} reset={() => undefined} />);

    expect(
      screen.getByRole("heading", { name: /this page didn't load/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /go home/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();

    render(<NotFound />);

    expect(screen.getByRole("heading", { name: "404" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /page not found/i }),
    ).toBeInTheDocument();

    errorSpy.mockRestore();
  });

  it("publishes uTrace metadata rather than generic placeholder metadata", () => {
    expect(homeMetadata.title).toBe(
      "uTrace — Ship features your customers actually want",
    );
    expect(homeMetadata.description).toContain("build solutions those same users validate");

  });
});
