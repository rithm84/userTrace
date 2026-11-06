import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { WhereItHelps } from "./WhereItHelps";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function reduceMotion(): void {
  const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  vi.spyOn(window, "matchMedia").mockReturnValue({ ...mediaQuery, matches: true });
}

it("opens a case from its thumbnail at the start of its problem and sends the open case back to the thumbnails", () => {
  render(<WhereItHelps />);
  expect(screen.getByTestId("helps-page")).toHaveAttribute("data-open", "failure");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "concern");
  expect(screen.getByTestId("helps-export")).toHaveAttribute("data-version", "problem");
  expect(screen.getByTestId("helps-step-0")).toHaveAttribute("data-status", "current");
  expect(screen.queryByTestId("helps-thumb-failure")).toBeNull();
  fireEvent.click(screen.getByTestId("helps-thumb-capability"));
  expect(screen.getByTestId("helps-page")).toHaveAttribute("data-open", "capability");
  expect(screen.getByTestId("helps-example")).toHaveAttribute("data-situation", "capability");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "listening");
  expect(screen.getByTestId("helps-drop")).toHaveAttribute("data-version", "problem");
  expect(screen.getByTestId("helps-thumb-failure")).toBeInTheDocument();
  expect(screen.queryByTestId("helps-thumb-capability")).toBeNull();
});

it("jumps to each step from the rail, from the problem through the build to the fixed version", () => {
  render(<WhereItHelps />);
  fireEvent.click(screen.getByRole("button", { name: "Fix" }));
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "building");
  expect(screen.getByTestId("helps-step-0")).toHaveAttribute("data-status", "complete");
  expect(screen.getByTestId("helps-step-1")).toHaveAttribute("data-status", "current");
  expect(screen.getByTestId("helps-export")).toHaveAttribute("data-version", "problem");
  fireEvent.click(screen.getByRole("button", { name: "Validate" }));
  expect(screen.getByTestId("helps-export")).toHaveAttribute("data-version", "fixed");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "listening");
  expect(screen.getByRole("button", { name: "Validate" })).toHaveAttribute("aria-current", "step");
  fireEvent.click(screen.getByTestId("helps-play"));
  expect(screen.getByTestId("helps-play")).toHaveAccessibleName("Play the case");
});

it("moves keyboard focus across the thumbnails and shows each step's still under reduced motion", () => {
  reduceMotion();
  render(<WhereItHelps />);
  expect(screen.queryByTestId("helps-play")).toBeNull();
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
  // the first step's still: the hand-built sheet, with uTrace asking about it
  expect(screen.getByTestId("helps-sheet")).toHaveAttribute("data-version", "problem");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "listening");
  expect(screen.getByText("week35.csv")).toBeInTheDocument();
  // the last step's still: the comparison inside the product, switched on, and the case complete
  fireEvent.click(screen.getByRole("button", { name: "Validate" }));
  expect(screen.getByTestId("helps-sheet")).toHaveAttribute("data-version", "fixed");
  expect(screen.getByRole("switch", { name: "Compare to previous week" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByTestId("helps-agent")).toHaveAttribute("data-state", "completion");
  expect(screen.getByTestId("helps-steps")).toHaveAttribute("data-step", "3");
});

it("adds the alert the customer asked for, and its first ping, in the capability's fixed version", () => {
  reduceMotion();
  render(<WhereItHelps />);
  fireEvent.click(screen.getByTestId("helps-thumb-capability"));
  expect(screen.queryByTestId("helps-slack")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Validate" }));
  expect(screen.getByTestId("helps-drop")).toHaveAttribute("data-version", "fixed");
  expect(screen.getByRole("switch", { name: "Slack alert" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByTestId("helps-slack")).toHaveTextContent("Conversion is 1.44%, under your 2% alert.");
});
