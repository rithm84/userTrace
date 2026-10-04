import { act, render, screen } from "@testing-library/react";
import { motionValue } from "motion/react";

import { JourneyAside, SkipAhead, StripProgress } from "@/components/landing/journey/JourneyAside";
import { scrollAtTime, scrollLength, timeAtScroll } from "@/components/landing/journey/scrollTimeline";
import { CUE, STRIP_SEGMENTS } from "@/components/landing/strip/cues";

describe("the column beside the journey", () => {
  it("counts the panels the strip has drawn, and keeps moving through a finished panel's rest", () => {
    const scroll = motionValue(0);
    const { rerender } = render(<JourneyAside left={900}><StripProgress scroll={scroll} drawn={0} /></JourneyAside>);
    // the page caught up with the scroll
    const follow = (px: number): void => {
      act(() => scroll.set(px));
      rerender(<JourneyAside left={900}><StripProgress scroll={scroll} drawn={timeAtScroll(px, STRIP_SEGMENTS)} /></JourneyAside>);
    };
    const progress = screen.getByTestId("strip-progress");
    expect(progress).toHaveTextContent("Introduction");
    follow(scrollAtTime(CUE.ritual + 100, STRIP_SEGMENTS));
    expect(progress).toHaveTextContent("2 of 8");
    // the second panel's last frame, then partway through its rest: the page holds, the count does not
    const lastFrame = scrollAtTime(CUE.reset - 1, STRIP_SEGMENTS);
    follow(lastFrame);
    const holding = Number(progress.dataset.done);
    follow(lastFrame + 150);
    expect(Number(progress.dataset.done)).toBeGreaterThan(holding);
    expect(progress).toHaveTextContent("2 of 8");
    // a fast scroll to the strip's end: the count waits for the panels to arrive, then is full
    act(() => scroll.set(scrollLength(STRIP_SEGMENTS) - 150));
    expect(progress).toHaveTextContent("2 of 8");
    follow(scrollLength(STRIP_SEGMENTS) - 150);
    expect(progress).toHaveTextContent("8 of 8");
    expect(progress).toHaveAttribute("data-done", "8.00");
  });

  it("offers a way past the strip and the story", () => {
    render(<JourneyAside left={null}><SkipAhead /></JourneyAside>);
    expect(screen.getByTestId("journey-skip-ahead")).toHaveAttribute("href", "#where-it-helps");
    expect(screen.getByTestId("journey-skip-ahead")).toHaveTextContent("Skip ahead");
  });
});
