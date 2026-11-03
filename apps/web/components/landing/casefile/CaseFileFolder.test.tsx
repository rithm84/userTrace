import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { CaseFileSection } from "./CaseFileSection";

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
