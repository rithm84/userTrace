import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { motionValue } from "motion/react";
import { createElement } from "react";
import { vi } from "vitest";

import { CATCH_UP_RATE, catchUp, MIN_SEGMENT_MS } from "@/components/landing/journey/scrollTimeline";
import { useScrollTimeline } from "@/components/landing/journey/useScrollTimeline";
import { CUE, INTRO_EXIT, PANEL_CUE, panelsLanded, panelsProgress, STRIP_DURATION, STRIP_SEGMENTS } from "@/components/landing/strip/cues";
import { Strip } from "@/components/landing/strip/Strip";
import { PAGE, PANELS, panelBox } from "@/components/landing/strip/stripGeometry";

vi.mock("@rive-app/react-webgl2", () => ({
  useRive: () => ({ rive: null, RiveComponent: () => createElement("canvas") }),
  useViewModelInstanceNumber: () => ({ value: 0, setValue: () => undefined }),
  useViewModelInstanceEnum: () => ({ value: "idle", values: [], setValue: () => undefined }),
}));

describe("the comic page", () => {
  it("keeps every panel inside the page and its tier, with no two panels overlapping", () => {
    for (const shape of PANELS) {
      for (const [x, y] of shape.points) {
        expect(x).toBeGreaterThanOrEqual(0);
        expect(x).toBeLessThanOrEqual(PAGE.width);
        expect(y).toBeGreaterThanOrEqual(0);
        expect(y).toBeLessThanOrEqual(PAGE.height);
      }
    }
    for (const a of PANELS) {
      for (const b of PANELS) {
        if (a === b) continue;
        const boxA = panelBox(a.id);
        const boxB = panelBox(b.id);
        const sameTier = boxA.top === boxB.top;
        if (!sameTier) {
          const apart = boxA.top >= boxB.top + boxB.height || boxB.top >= boxA.top + boxA.height;
          expect(apart).toBe(true);
          continue;
        }
        // neighbours share a slanted gutter: at every height the left panel ends before the right one starts
        const [left, right] = boxA.left < boxB.left ? [a, b] : [b, a];
        const leftEdge = (y: number): number => left.points[1][0] + ((left.points[2][0] - left.points[1][0]) * (y - left.points[1][1])) / (left.points[2][1] - left.points[1][1]);
        const rightEdge = (y: number): number => right.points[0][0] + ((right.points[3][0] - right.points[0][0]) * (y - right.points[0][1]) / (right.points[3][1] - right.points[0][1]));
        for (const y of [boxA.top, boxA.top + boxA.height / 2, boxA.top + boxA.height]) {
          expect(rightEdge(y) - leftEdge(y)).toBeGreaterThanOrEqual(PAGE.gutter - 0.01);
        }
      }
    }
  });

  it("arrives in reading order and ends after the last beat", () => {
    const cues = PANELS.map((shape) => PANEL_CUE[shape.id]);
    for (let i = 1; i < cues.length; i++) expect(cues[i]).toBeGreaterThan(cues[i - 1]);
    expect(STRIP_DURATION).toBeGreaterThan(CUE.clickCaption);
  });

  it("renders the finished page, where the scroll has pressed the one real button", () => {
    const onJoin = vi.fn();
    render(<Strip elapsed={STRIP_DURATION} live reducedMotion onJoin={onJoin} />);
    expect(screen.getByTestId("strip-page")).toHaveAttribute("data-finished", "true");
    expect(screen.queryByTestId("strip-intro")).toBeNull();
    expect(screen.getByTestId("strip-request-message").textContent).toContain("every Monday");
    expect(screen.getByTestId("strip-corroborate-count").textContent).toContain("3");
    expect(screen.getByTestId("strip-signal-embed").textContent).toContain("Product Signal");
    for (const cameo of ["strip-mint", "strip-lilac", "strip-sky", "strip-coral", "strip-peach"]) expect(screen.getByTestId(cameo)).toBeInTheDocument();
    expect(screen.getByTestId("strip-caption-click").className).toContain("is-on");
    // the used button is Discord's receipt, no longer a control
    const button = screen.getByTestId("strip-join");
    expect(button).toBeDisabled();
    expect(button).toHaveClass("is-joined");
    expect(button).toHaveTextContent("Joined");
    button.click();
    expect(onJoin).not.toHaveBeenCalled();
  });

  it("keeps Join interview a real shortcut into the story until the scroll presses it", () => {
    const onJoin = vi.fn();
    const props = { live: true, reducedMotion: false, onJoin };
    const { rerender } = render(<Strip {...props} elapsed={CUE.clickPress - 1} />);
    const button = screen.getByTestId("strip-join");
    // the cursor has landed on it: hover colour, not yet pressed
    expect(button).toHaveClass("is-hover");
    expect(button).toHaveTextContent("Join interview");
    fireEvent.click(button);
    expect(onJoin).toHaveBeenCalledTimes(1);
    rerender(<Strip {...props} elapsed={CUE.clickPress} />);
    expect(screen.getByTestId("strip-join")).toBeDisabled();
    // scrolling back un-presses it
    rerender(<Strip {...props} elapsed={CUE.clickPress - 1} />);
    expect(screen.getByTestId("strip-join")).toBeEnabled();
  });

  it("introduces the case only in the middle panel, clears it, then starts in reading order", () => {
    const props = { live: true, reducedMotion: false, onJoin: () => undefined };
    const { container, rerender } = render(<Strip {...props} elapsed={INTRO_EXIT - 1} />);
    expect(screen.getByTestId("strip-intro").closest("section")).toHaveAttribute("data-panel", "echo");
    expect(screen.getByTestId("strip-cast")).toHaveAttribute("data-typed", "true");
    expect(container.querySelectorAll('[data-panel][data-on="true"]')).toHaveLength(1);
    expect(screen.queryByTestId("strip-join")).toBeNull();
    rerender(<Strip {...props} elapsed={INTRO_EXIT + 300} />);
    expect(screen.getByTestId("strip-intro")).toHaveStyle({ opacity: "0.5" });
    expect(container.querySelectorAll('[data-panel][data-on="true"]')).toHaveLength(0);
    rerender(<Strip {...props} elapsed={CUE.desk} />);
    expect(screen.queryByTestId("strip-intro")).toBeNull();
    expect(container.querySelector('[data-panel="desk"]')).toHaveAttribute("data-on", "true");
    expect(container.querySelector('[data-panel="echo"]')).toHaveAttribute("data-on", "false");
    rerender(<Strip {...props} elapsed={CUE.echo} />);
    expect(container.querySelector('[data-panel="echo"]')).toHaveAttribute("data-on", "true");
    expect(screen.queryByTestId("strip-intro")).toBeNull();
  });
});

describe("the strip's scroll timeline", () => {
  it("closes a small gap at the ordinary pace, in either direction, and stops with the reader", () => {
    const from = CUE.desk + 1000;
    expect(catchUp(from, from + 1500, 16, STRIP_SEGMENTS, true)).toBe(from + 16 * CATCH_UP_RATE);
    expect(catchUp(from, from - 900, 16, STRIP_SEGMENTS, true)).toBe(from - 16 * CATCH_UP_RATE);
    expect(catchUp(from, from + 10, 16, STRIP_SEGMENTS, true)).toBe(from + 10);
    expect(catchUp(from, from, 16, STRIP_SEGMENTS, true)).toBe(from);
    // a stalled frame moves no further than a short one would
    expect(catchUp(from, CUE.ritual - 1, 5000, STRIP_SEGMENTS, true)).toBe(catchUp(from, CUE.ritual - 1, 100, STRIP_SEGMENTS, true));
  });

  it("closes a larger gap faster but never skips a panel on screen, so they arrive one at a time", () => {
    const small = catchUp(CUE.desk + 10, CUE.desk + 1500, 16, STRIP_SEGMENTS, true) - (CUE.desk + 10);
    const large = catchUp(CUE.desk + 10, CUE.echo + 500, 16, STRIP_SEGMENTS, true) - (CUE.desk + 10);
    expect(large).toBeGreaterThan(small);
    // each panel passed takes at least MIN_SEGMENT_MS, so two never arrive together
    for (const [start, end] of [[CUE.ritual, CUE.reset], [CUE.reset, CUE.request]] as const) {
      let shown = start;
      let frames = 0;
      while (shown < end) {
        shown = catchUp(shown, STRIP_DURATION, 16, STRIP_SEGMENTS, true);
        frames++;
      }
      expect(frames * 16).toBeGreaterThanOrEqual(MIN_SEGMENT_MS - 16);
    }
    // the same on the way back
    expect(catchUp(CUE.click + 10, CUE.ritual + 5, 16, STRIP_SEGMENTS, true)).toBeGreaterThan(CUE.invite);
    // off screen there is nothing to play, so it lands at once
    expect(catchUp(CUE.desk + 10, CUE.echo + 500, 16, STRIP_SEGMENTS, false)).toBe(CUE.echo + 500);
  });

  it("rests on the typed introduction, not on the blank page after it", () => {
    expect(STRIP_SEGMENTS[1]).toBe(INTRO_EXIT);
    expect(STRIP_SEGMENTS).not.toContain(CUE.desk);
    expect(STRIP_SEGMENTS[STRIP_SEGMENTS.length - 1]).toBe(STRIP_DURATION);
  });

  it("follows the scroll's moment frame by frame and settles on it", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    try {
      const target = motionValue(CUE.desk);
      const { result } = renderHook(() => useScrollTimeline(target, STRIP_SEGMENTS, true, false));
      expect(result.current).toBe(CUE.desk);
      act(() => {
        target.set(CUE.desk + 2000);
      });
      act(() => {
        vi.advanceTimersByTime(160);
      });
      expect(result.current).toBeGreaterThan(CUE.desk);
      expect(result.current).toBeLessThan(CUE.desk + 2000);
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(result.current).toBe(CUE.desk + 2000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never plays back what the reader has seen, and still plays what passed off screen", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    try {
      const target = motionValue(CUE.ritual);
      const { result, rerender } = renderHook(({ watching }: { watching: boolean }) => useScrollTimeline(target, STRIP_SEGMENTS, watching, false), { initialProps: { watching: true } });
      const play = (to: number): void => {
        act(() => {
          target.set(to);
        });
        act(() => {
          vi.advanceTimersByTime(4000);
        });
      };
      // watched into the second panel, then scrolled back within it: it holds
      play(CUE.ritual + 3000);
      play(CUE.ritual + 500);
      expect(result.current).toBe(CUE.ritual + 3000);
      // back into the first panel's stretch from behind: it shows its finished frame, not a rewind
      play(CUE.desk + 200);
      expect(result.current).toBeGreaterThan(CUE.ritual - 100);
      expect(result.current).toBeLessThan(CUE.ritual);
      // passed while the page was off screen, the echo panel was never drawn
      rerender({ watching: false });
      play(CUE.reply + 1000);
      play(CUE.reset + 500);
      // so scrolling down to it on screen plays it
      rerender({ watching: true });
      play(CUE.echo + 500);
      expect(result.current).toBe(CUE.echo + 500);
      // and off screen nothing is held: the page follows the scroll
      rerender({ watching: false });
      play(CUE.invite + 2000);
      play(CUE.invite + 200);
      expect(result.current).toBe(CUE.invite + 200);
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts where the scroll is when it comes on screen, as after a reload, instead of playing up to it", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    try {
      const target = motionValue(0);
      const { result, rerender } = renderHook(({ watching }: { watching: boolean }) => useScrollTimeline(target, STRIP_SEGMENTS, watching, false), { initialProps: { watching: false } });
      // the restored scroll arrives before the page's first frame, and the page comes on screen with it
      act(() => {
        target.set(CUE.invite + 500);
      });
      rerender({ watching: true });
      act(() => {
        vi.advanceTimersByTime(20);
      });
      expect(result.current).toBe(CUE.invite + 500);
    } finally {
      vi.useRealTimers();
    }
  });

  it("counts panels as they land and reports the finished page under reduced motion", () => {
    expect(panelsLanded(0)).toBe(0);
    expect(panelsLanded(CUE.reset)).toBe(3);
    expect(panelsLanded(STRIP_DURATION)).toBe(8);
    expect(panelsProgress(0)).toBe(0);
    expect(panelsProgress(CUE.desk)).toBe(0);
    expect(panelsProgress((CUE.desk + CUE.ritual) / 2)).toBeCloseTo(0.5, 5);
    expect(panelsProgress(CUE.reset)).toBe(2);
    expect(panelsProgress(STRIP_DURATION)).toBe(8);
    const { result } = renderHook(() => useScrollTimeline(motionValue(0), STRIP_SEGMENTS, true, true));
    expect(result.current).toBe(STRIP_DURATION);
  });
});
