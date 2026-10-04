/** Component interaction proof; the shared test harness does not verify the rendered Rive rig or browser layout. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { WhereItHelps } from "./WhereItHelps";

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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("opens a case from its thumbnail and sends the open case back to the thumbnails", () => {
  render(<WhereItHelps />);
  expect(screen.getByTestId("helps-page")).toHaveAttribute("data-open", "failure");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "investigating");
  expect(screen.queryByTestId("helps-thumb-failure")).toBeNull();
  fireEvent.click(screen.getByTestId("helps-thumb-capability"));
  expect(screen.getByTestId("helps-page")).toHaveAttribute("data-open", "capability");
  expect(screen.getByTestId("helps-example")).toHaveAttribute("data-situation", "capability");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "listening");
  expect(screen.getByTestId("helps-thumb-failure")).toBeInTheDocument();
  expect(screen.queryByTestId("helps-thumb-capability")).toBeNull();
});

it("moves keyboard focus across the thumbnails and parks the sticker under reduced motion", () => {
  const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  vi.spyOn(window, "matchMedia").mockReturnValue({ ...mediaQuery, matches: true });
  render(<WhereItHelps />);
  const first = screen.getByTestId("helps-thumb-capability");
  first.focus();
  fireEvent.keyDown(first, { key: "ArrowDown" });
  expect(screen.getByTestId("helps-thumb-workaround")).toHaveFocus();
  fireEvent.keyDown(screen.getByTestId("helps-thumb-workaround"), { key: "ArrowDown" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(first, { key: "End" });
  expect(screen.getByTestId("helps-thumb-workaround")).toHaveFocus();
  fireEvent.click(screen.getByTestId("helps-thumb-workaround"));
  expect(screen.getByTestId("landing-helps")).toHaveAttribute("data-reduced-motion", "true");
  expect(screen.getByTestId("helps-page")).toHaveAttribute("data-open", "workaround");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "concern");
  expect(screen.getByText("=C3/B3-1")).toBeInTheDocument();
});
