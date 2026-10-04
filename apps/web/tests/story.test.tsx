import { act, fireEvent, render, screen } from "@testing-library/react";
import { motionValue } from "motion/react";
import { createElement } from "react";
import { vi } from "vitest";

import { scrollAtTime } from "@/components/landing/journey/scrollTimeline";
import { BEATS, BEAT_STARTS, CHAPTER_STARTS, CHAPTERS, STORY_DURATION, STORY_SEGMENTS, beatProgress, chapterProgress, lastBeatOf, selectBeat } from "@/components/landing/story/beats";
import { Story } from "@/components/landing/story/Story";

vi.mock("@rive-app/react-webgl2", () => ({
  useRive: () => ({ rive: null, RiveComponent: () => createElement("canvas") }),
  useViewModelInstanceNumber: () => ({ value: 0, setValue: () => undefined }),
  useViewModelInstanceEnum: () => ({ value: "idle", values: [], setValue: () => undefined }),
}));

describe("the story's beat table", () => {
  it("covers the five chapters in order, every beat on a real surface with its own character", () => {
    const order = CHAPTERS.map((chapter) => chapter.id);
    let last = -1;
    for (const beat of BEATS) {
      const at = order.indexOf(beat.chapter);
      expect(at).toBeGreaterThanOrEqual(last);
      last = at;
      expect(["session", "preview", "workspace", "casefile"]).toContain(beat.surface);
      if (beat.surface === "session") expect(beat.agentLine).toBeTruthy();
    }
    expect(new Set(BEATS.map((beat) => beat.chapter)).size).toBe(CHAPTERS.length);
    expect(BEATS[0].surface).toBe("session");
    expect(BEATS[BEATS.length - 1].surface).toBe("casefile");
  });

  it("gives the scroll one segment per beat, ending on the finished story", () => {
    expect(STORY_SEGMENTS).toEqual([...BEAT_STARTS, STORY_DURATION]);
  });

  it("selects beats from the clock and reports progress inside them", () => {
    expect(selectBeat(0)).toBe(0);
    expect(selectBeat(STORY_DURATION)).toBe(BEATS.length - 1);
    expect(beatProgress(BEATS[0].duration / 2, 0)).toBeCloseTo(0.5, 5);
    expect(lastBeatOf("ship")).toBe(BEATS.length - 1);
    // the rail fill follows the chapter, so it never snaps back between an iterate beat and the next
    const repair = BEATS.findIndex((beat) => beat.id === "repair");
    expect(chapterProgress(BEAT_STARTS[repair] - 1, "iterate")).toBeLessThan(chapterProgress(BEAT_STARTS[repair] + 1, "iterate"));
    expect(chapterProgress(BEAT_STARTS[repair] - 1, "iterate")).toBeLessThan(1);
  });
});

describe("the story page", () => {
  it("keeps a restored completed Ship chapter in its held pose when scrolled back into view", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(<Story elapsed={STORY_DURATION} active={false} scroll={null} onChapter={null} reducedMotion={false} />);
      rerender(<Story elapsed={STORY_DURATION} active scroll={null} onChapter={null} reducedMotion={false} />);
      act(() => vi.advanceTimersByTime(1400));
      expect(screen.getByTestId("story-rail-agent")).toHaveAttribute("data-state", "ship");
      rerender(<Story elapsed={STORY_DURATION} active={false} scroll={null} onChapter={null} reducedMotion={false} />);
      rerender(<Story elapsed={STORY_DURATION} active scroll={null} onChapter={null} reducedMotion={false} />);
      act(() => vi.advanceTimersByTime(3000));
      expect(screen.getByTestId("story-rail-agent")).toHaveAttribute("data-state", "ship");
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens on the session tab with the greeting, inside the browser frame, with the chapter bar on Interview", () => {
    const { rerender } = render(<Story elapsed={0} active={false} scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.queryByTestId("story-rail-status")).toBeNull();
    rerender(<Story elapsed={0} active scroll={null} onChapter={null} reducedMotion={false} />);
    const page = screen.getByTestId("story-page");
    expect(page).toHaveAttribute("data-beat", "greet");
    expect(screen.getByTestId("story-frame")).toHaveAttribute("data-surface", "session");
    expect(screen.getByTestId("story-rail")).toHaveAttribute("data-chapter", "interview");
    // the rail carries the character that is on screen, in the same state
    expect(screen.getByTestId("story-rail-agent")).toHaveAttribute("data-state", screen.getByTestId("story-session-agent").getAttribute("data-state") ?? "");
    // the transcript names who says each line: the agent's greeting, then Maya's answer in the beat's second half
    rerender(<Story elapsed={BEATS[0].duration * 0.5} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getByTestId("story-turn-greet-agent")).toHaveTextContent("Ivo" + BEATS[0].agentLine);
    expect(screen.queryByTestId("story-turn-greet-maya")).toBeNull();
    rerender(<Story elapsed={BEATS[0].duration - 1} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getByTestId("story-turn-greet-maya")).toHaveTextContent("Maya" + BEATS[0].mayaLine);
    // one live call: the later session beat keeps every earlier turn above its own, including what Maya said in the preview
    rerender(<Story elapsed={BEAT_STARTS[4] - 1} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getAllByTestId(/^story-turn-/).map((turn) => turn.dataset.testid)).toEqual(["story-turn-greet-agent", "story-turn-greet-maya", "story-turn-reset-maya", "story-turn-agree-agent", "story-turn-agree-maya"]);
  });

  it("reports chapter beats, and completes the final chapter only when its last beat ends", () => {
    const { rerender } = render(<Story elapsed={BEAT_STARTS[1]} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getByTestId("story-rail-beats")).toHaveTextContent("2 of 4");
    expect(screen.getByTestId("story-chapter-progress")).toHaveAttribute("aria-valuenow", "24");
    expect(screen.getByTestId("story-stop-build")).toHaveAttribute("data-status", "upcoming");
    expect(screen.queryByTestId("story-rail-status")).toBeNull();

    const finalStart = BEAT_STARTS[BEATS.length - 1];
    rerender(<Story elapsed={finalStart} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getByTestId("story-stop-interview")).toHaveAttribute("data-status", "complete");
    expect(screen.getByTestId("story-stop-ship")).toHaveAttribute("data-status", "current");
    expect(screen.getByTestId("story-chapter-progress")).toHaveAttribute("aria-valuenow", "0");
    expect(screen.queryByTestId("story-rail-status")).toBeNull();
    rerender(<Story elapsed={STORY_DURATION - 1} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getByTestId("story-chapter-progress")).toHaveAttribute("aria-valuenow", "99");
    rerender(<Story elapsed={STORY_DURATION} active scroll={null} onChapter={null} reducedMotion={false} />);
    expect(screen.getByTestId("story-page")).toHaveAttribute("data-playback", "finished");
    expect(screen.getByTestId("story-stop-ship")).toHaveAttribute("data-status", "complete");
    expect(screen.getByTestId("story-chapter-progress")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByTestId("story-rail-status")).toHaveTextContent("Complete");
  });

  it("renders the five chapter stills under reduced motion, ending on the Case File and the PR", () => {
    render(<Story elapsed={STORY_DURATION} active scroll={null} onChapter={null} reducedMotion />);
    expect(screen.getAllByTestId("story-frame")).toHaveLength(CHAPTERS.length);
    expect(screen.getByTestId("story-casefile").textContent).toContain("Case File");
    expect(screen.getByTestId("story-merge-note").textContent).toContain("Your team merges");
    expect(screen.getByTestId("story-case-closed").textContent).toContain("Case closed");
    expect(screen.queryByTestId("story-builder")).toBeNull();
    const tags = screen.getAllByTestId("story-validation-tag").map((tag) => tag.textContent ?? "");
    expect(tags[0]).toContain("partially validated");
    expect(tags[1]).toContain("validated · all seven back");
    expect(screen.getByTestId("story-gate-stamp").textContent).toContain("Approved");
    expect(screen.getAllByTestId("story-rail-status").every((status) => status.textContent === "Complete")).toBe(true);
  });
});

describe("the scrolled story's rail", () => {
  it("takes the reader to a chapter from its stop", () => {
    const onChapter = vi.fn();
    render(<Story elapsed={BEAT_STARTS[1]} active scroll={motionValue(0)} onChapter={onChapter} reducedMotion={false} />);
    fireEvent.click(screen.getByTestId("story-jump-validate"));
    expect(onChapter).toHaveBeenCalledWith("validate");
    expect(screen.getByTestId("story-jump-interview")).toHaveAccessibleName("Go to the Interview chapter");
  });

  it("keeps filling the active chapter's ring while a finished beat rests, but never past what is drawn", () => {
    // the first beat's last frame: its scroll is spent, its rest is not
    const lastFrame = BEAT_STARTS[1] - 1;
    const scroll = motionValue(scrollAtTime(lastFrame, STORY_SEGMENTS) + 1);
    const { rerender } = render(<Story elapsed={lastFrame} active scroll={scroll} onChapter={() => undefined} reducedMotion={false} />);
    const ringAfter = (px: number): number => {
      act(() => scroll.set(px));
      return Number.parseFloat(document.querySelector(".st-stop-ring-fill")?.getAttribute("stroke-dashoffset") ?? "NaN");
    };
    const resting = ringAfter(scroll.get());
    expect(ringAfter(scroll.get() + 200)).toBeLessThan(resting);
    // the scroll reaches the chapter's end while the story still draws its first beat: the ring waits
    const chapterEnd = scrollAtTime(CHAPTER_STARTS[1], STORY_SEGMENTS);
    expect(ringAfter(chapterEnd)).toBeGreaterThan(0);
    // drawn through, the whole chapter's scroll, rests included, fills it
    rerender(<Story elapsed={CHAPTER_STARTS[1] - 1} active scroll={scroll} onChapter={() => undefined} reducedMotion={false} />);
    expect(ringAfter(chapterEnd)).toBeCloseTo(0, 5);
  });
});
