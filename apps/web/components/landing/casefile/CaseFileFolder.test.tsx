/** Component interaction proof; the shared test harness does not verify the rendered Rive rig or browser layout. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { CaseFileSection } from "./CaseFileSection";

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
});

it("opens on the summary sheet and pulls other sheets by their dividers or the summary's button", () => {
  render(<CaseFileSection />);
  const folder = screen.getByTestId("casefile-folder");
  expect(folder).toHaveAttribute("data-sheet", "summary");
  expect(screen.getByTestId("casefile-stamp").textContent).toBe("validated");
  fireEvent.click(screen.getByTestId("casefile-divider-risk"));
  expect(folder).toHaveAttribute("data-sheet", "risk");
  expect(screen.getByTestId("casefile-divider-risk")).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByTestId("casefile-divider-summary"));
  fireEvent.click(screen.getByTestId("casefile-view-pr"));
  expect(folder).toHaveAttribute("data-sheet", "change");
  expect(screen.getByTestId("casefile-sheet").textContent).toContain("Pull request #482");
});

it("moves selection with the arrow keys along the dividers", () => {
  render(<CaseFileSection />);
  const summary = screen.getByTestId("casefile-divider-summary");
  summary.focus();
  fireEvent.keyDown(summary, { key: "ArrowDown" });
  expect(screen.getByTestId("casefile-folder")).toHaveAttribute("data-sheet", "evidence");
  expect(screen.getByTestId("casefile-divider-evidence")).toHaveFocus();
  fireEvent.keyDown(screen.getByTestId("casefile-divider-evidence"), { key: "End" });
  expect(screen.getByTestId("casefile-folder")).toHaveAttribute("data-sheet", "next");
  fireEvent.keyDown(screen.getByTestId("casefile-divider-next"), { key: "ArrowDown" });
  expect(screen.getByTestId("casefile-folder")).toHaveAttribute("data-sheet", "summary");
});
