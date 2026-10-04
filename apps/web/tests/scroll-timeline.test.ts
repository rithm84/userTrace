import { REST_PX, SCROLL_PX_PER_MS, heldMoment, marksProgress, scrollAtTime, scrollLength, shownScroll, timeAtScroll } from "@/components/landing/journey/scrollTimeline";

/** Two segments: 0 to 1000 ms, then 1000 to 3000 ms. */
const SEGMENTS = [0, 1000, 3000] as const;

describe("a scroll-driven page's timeline", () => {
  it("takes its authored time at the shared pace plus a rest after every segment", () => {
    expect(scrollLength(SEGMENTS)).toBe(3000 * SCROLL_PX_PER_MS + 2 * REST_PX);
  });

  it("plays each segment through its scroll, then holds its last frame for the rest", () => {
    const first = 1000 * SCROLL_PX_PER_MS;
    expect(timeAtScroll(-50, SEGMENTS)).toBe(0);
    expect(timeAtScroll(first / 2, SEGMENTS)).toBeCloseTo(500, 5);
    // resting on the first segment's last frame: the second has not begun
    expect(timeAtScroll(first + 1, SEGMENTS)).toBe(999);
    expect(timeAtScroll(first + REST_PX - 1, SEGMENTS)).toBe(999);
    expect(timeAtScroll(first + REST_PX, SEGMENTS)).toBe(1000);
    // the last rest holds the finished page, and so does everything past it
    const second = first + REST_PX + 2000 * SCROLL_PX_PER_MS;
    expect(timeAtScroll(second + 1, SEGMENTS)).toBe(3000);
    expect(timeAtScroll(second + REST_PX + 500, SEGMENTS)).toBe(3000);
  });

  it("refuses segments that do not start at zero and increase", () => {
    expect(() => scrollLength([100, 200])).toThrow(RangeError);
    expect(() => timeAtScroll(0, [0, 500, 500])).toThrow(RangeError);
  });

  it("finds the scroll at which a moment first plays, the inverse of the timeline", () => {
    for (const ms of [0, 400, 999, 1000, 2500, 3000]) expect(timeAtScroll(scrollAtTime(ms, SEGMENTS), SEGMENTS)).toBeCloseTo(ms, 5);
    // the second segment starts after the first one's rest
    expect(scrollAtTime(1000, SEGMENTS)).toBe(1000 * SCROLL_PX_PER_MS + REST_PX);
  });

  it("counts progress through marks continuously, rests included", () => {
    const marks = [0, 1000];
    const second = scrollAtTime(1000, SEGMENTS);
    expect(marksProgress(0, marks, SEGMENTS)).toBe(0);
    // the first mark's stretch runs to the second, so its rest keeps the count moving
    const lastFrame = scrollAtTime(999, SEGMENTS);
    expect(marksProgress(lastFrame + REST_PX / 2, marks, SEGMENTS)).toBeGreaterThan(marksProgress(lastFrame, marks, SEGMENTS));
    expect(marksProgress(second, marks, SEGMENTS)).toBe(1);
    // the finished page counts full through its last rest
    expect(marksProgress(scrollAtTime(3000, SEGMENTS), marks, SEGMENTS)).toBe(2);
    expect(marksProgress(scrollLength(SEGMENTS), marks, SEGMENTS)).toBe(2);
  });

  it("shows progress no further than the page has drawn", () => {
    const rest = scrollAtTime(999, SEGMENTS) + REST_PX / 2;
    // caught up, resting: the scroll itself
    expect(shownScroll(rest, timeAtScroll(rest, SEGMENTS), SEGMENTS)).toBe(rest);
    // the scroll ran ahead while the page still draws the first segment
    expect(shownScroll(scrollLength(SEGMENTS), 500, SEGMENTS)).toBe(scrollAtTime(500, SEGMENTS));
    // scrolled back while the page holds the second segment finished
    expect(shownScroll(scrollAtTime(1500, SEGMENTS), 3000, SEGMENTS)).toBe(scrollAtTime(3000, SEGMENTS));
  });

  it("holds a segment at the furthest point watched, and moves freely between segments", () => {
    const watched = [700, 1000];
    // within the first segment, behind what was watched: hold
    expect(heldMoment(200, watched, SEGMENTS)).toBe(700);
    // ahead of it: play on
    expect(heldMoment(900, watched, SEGMENTS)).toBe(900);
    // the second segment was never watched past its start: follow the scroll
    expect(heldMoment(1500, watched, SEGMENTS)).toBe(1500);
    expect(() => heldMoment(0, [0], SEGMENTS)).toThrow(RangeError);
  });
});
