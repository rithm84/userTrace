/** Component interaction proof; the shared test harness does not verify the rendered Rive rig or browser layout. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";

import { Closing } from "./Closing";
import { WaitlistProvider } from "../Waitlist";

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

// jsdom has no pointer capture; the grip takes it so a fast pull keeps its pointer
beforeAll(() => {
  for (const name of ["setPointerCapture", "releasePointerCapture"]) {
    Object.defineProperty(HTMLElement.prototype, name, { configurable: true, writable: true, value: () => undefined });
  }
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const CAST = ["black", "mint", "sky", "lilac", "peach", "aqua"];
/** The spot, and where the six have dragged the glass on arrival, in stage px. */
const REST = { x: 610, y: 396 };
const PULLED = { x: 830, y: 396 };
const GRIP_LABEL = "Pull the magnifying glass into place";

/** The lens centre in stage px, read from its transform. */
function lensCentre(): { x: number; y: number } {
  const lens = screen.getByTestId("closing-lens");
  const match = /^translate3d\((-?[\d.]+)px, (-?[\d.]+)px, 0\)$/.exec(lens.style.transform);
  if (match === null) throw new Error(`The lens transform is not a translation: ${lens.style.transform}`);
  const radius = parseFloat(lens.style.width) / 2;
  return { x: parseFloat(match[1]) + radius, y: parseFloat(match[2]) + radius };
}

function castStates(): string[] {
  return CAST.map((colour) => screen.getByTestId(`closing-cast-${colour}`).getAttribute("data-state") ?? "");
}

it("arrives pulled right with no buttons; pulling the handle past the spot clicks the glass in, shows the buttons and freezes it there", async () => {
  render(<Closing />, { wrapper: WaitlistProvider });
  const section = screen.getByTestId("landing-closing");
  expect(section).toHaveAttribute("data-phase", "pulled");
  expect(lensCentre()).toEqual(PULLED);
  const headline = screen.getByRole("heading", { level: 2 });
  const copy = screen.getByTestId("closing-lens-copy");
  expect(headline.textContent).toBe("Give goodfeedbacka way forward.");
  expect(copy.textContent).toBe(headline.textContent);
  expect(parseFloat(copy.style.fontSize)).toBeGreaterThan(parseFloat(headline.style.fontSize));
  expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "false");
  expect(screen.getByTestId("closing-cue")).toBeInTheDocument();
  expect(screen.getByTestId("closing-coral")).toHaveAttribute("data-state", "concern");
  expect(castStates()).toEqual(CAST.map(() => "concern"));

  const grip = screen.getByRole("button", { name: GRIP_LABEL });
  fireEvent.pointerDown(grip, { pointerId: 1, clientX: 300, clientY: 520 });
  expect(section).toHaveAttribute("data-phase", "pulling");
  expect(screen.queryByTestId("closing-cue")).toBeNull();
  // the six resist: sixty pixels of pull move the glass, but by less than sixty, and sideways travel gives only a quarter
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 240, clientY: 560 });
  await waitFor(() => expect(lensCentre().x).toBeLessThan(PULLED.x - 5), { timeout: 3000 });
  const partway = lensCentre();
  expect(partway.x).toBeGreaterThan(PULLED.x - 60);
  expect(partway.y).toBeGreaterThan(REST.y);
  expect(partway.y).toBeLessThanOrEqual(REST.y + 10);
  expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "false");
  // pulled well past the spot, the six lose and the glass clicks in
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: -100, clientY: 520 });
  await waitFor(() => expect(section).toHaveAttribute("data-phase", "settled"), { timeout: 6000 });
  expect(lensCentre()).toEqual(REST);
  expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "true");
  expect(screen.queryByRole("button", { name: GRIP_LABEL })).toBeNull();
  expect(screen.queryByTestId("closing-cue")).toBeNull();
  expect(screen.getByTestId("closing-coral")).toHaveAttribute("data-state", "idle");
  expect(castStates()).toEqual(CAST.map(() => "idle"));
  expect(screen.getByTestId("closing-actions")).not.toHaveAttribute("inert");
  // frozen: the pointer wandering the section moves nothing
  fireEvent.pointerMove(section, { clientX: 900, clientY: 200 });
  fireEvent.pointerLeave(section);
  expect(lensCentre()).toEqual(REST);
  expect(section).toHaveAttribute("data-phase", "settled");
}, 10000);

it("takes ground back while the hand rests, and a glass let go short of the spot goes back to the six with the cue", async () => {
  render(<Closing />, { wrapper: WaitlistProvider });
  const grip = screen.getByTestId("closing-grip");
  expect(screen.getByTestId("closing-cue")).toHaveTextContent("Grab the handle and pull.");
  fireEvent.pointerDown(grip, { pointerId: 1, clientX: 300, clientY: 520 });
  fireEvent.pointerMove(grip, { pointerId: 1, clientX: 200, clientY: 520 });
  await waitFor(() => expect(lensCentre().x).toBeLessThan(PULLED.x - 45), { timeout: 3000 });
  // once the spring has settled under the resting hand, the glass only moves one way: back toward the six
  await new Promise((resolve) => setTimeout(resolve, 500));
  const held = lensCentre().x;
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const later = lensCentre().x;
  expect(later).toBeGreaterThan(held + 8);
  expect(later).toBeLessThan(PULLED.x);
  expect(screen.getByTestId("landing-closing")).toHaveAttribute("data-phase", "pulling");
  fireEvent.pointerUp(grip, { pointerId: 1, clientX: 200, clientY: 520 });
  expect(screen.getByTestId("landing-closing")).toHaveAttribute("data-phase", "pulled");
  expect(screen.getByTestId("closing-cue")).toHaveTextContent("That all you got?");
  // releasing capture after pointerup belongs to the same failed pull
  fireEvent.lostPointerCapture(grip, { pointerId: 1 });
  expect(screen.getByTestId("closing-cue")).toHaveTextContent("That all you got?");
  await waitFor(() => expect(lensCentre()).toEqual(PULLED), { timeout: 6000 });
  expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "false");
  expect(screen.getByTestId("closing-grip")).toBeInTheDocument();
  for (const pointerId of [2, 3]) {
    fireEvent.pointerDown(grip, { pointerId, clientX: 300, clientY: 520 });
    fireEvent.pointerMove(grip, { pointerId, clientX: 240, clientY: 520 });
    fireEvent.pointerUp(grip, { pointerId, clientX: 240, clientY: 520 });
    expect(screen.getByTestId("closing-cue")).toHaveTextContent("Pull harder!");
  }
}, 12000);

it("pulls the glass in from the keyboard and lands focus on the waitlist email field", async () => {
  render(<Closing />, { wrapper: WaitlistProvider });
  const grip = screen.getByRole("button", { name: GRIP_LABEL });
  grip.focus();
  fireEvent.keyDown(grip, { key: "Enter" });
  expect(screen.getByTestId("landing-closing")).toHaveAttribute("data-phase", "pulling");
  await waitFor(() => expect(screen.getByTestId("landing-closing")).toHaveAttribute("data-phase", "settled"), { timeout: 6000 });
  expect(lensCentre()).toEqual(REST);
  expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "true");
  expect(screen.getByTestId("closing-waitlist-email")).toHaveFocus();
}, 10000);

it("is already in its spot under reduced motion, buttons showing, nothing to pull, rigs parked idle", () => {
  const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  vi.spyOn(window, "matchMedia").mockReturnValue({ ...mediaQuery, matches: true });
  render(<Closing />, { wrapper: WaitlistProvider });
  const section = screen.getByTestId("landing-closing");
  expect(section).toHaveAttribute("data-reduced-motion", "true");
  expect(section).toHaveAttribute("data-phase", "settled");
  expect(lensCentre()).toEqual(REST);
  expect(screen.getByTestId("closing-actions")).toHaveAttribute("data-shown", "true");
  expect(screen.queryByRole("button", { name: GRIP_LABEL })).toBeNull();
  expect(screen.queryByTestId("closing-cue")).toBeNull();
  fireEvent.pointerMove(section, { clientX: 860, clientY: 470 });
  expect(lensCentre()).toEqual(REST);
  expect(screen.getByTestId("closing-coral")).toHaveAttribute("data-state", "idle");
  expect(castStates()).toEqual(CAST.map(() => "idle"));
});
