import { act, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { vi } from "vitest";

import { sceneAt, pointerAt, type SceneStep } from "@/components/landing/story/agentScene";
import { BEATS, BEAT_STARTS, CHAPTERS, JOIN_INDEX, STORY_DURATION, beatProgress, chapterProgress, lastBeatOf, selectBeat } from "@/components/landing/story/beats";
import { Demo } from "@/components/landing/story/Demo";
import { Story } from "@/components/landing/story/Story";

vi.mock("@rive-app/react-webgl2", () => ({
  useRive: () => ({ rive: null, RiveComponent: () => createElement("canvas") }),
  useViewModelInstanceNumber: () => ({ value: 0, setValue: () => undefined }),
  useViewModelInstanceEnum: () => ({ value: "idle", values: [], setValue: () => undefined }),
}));

const noop = (): void => undefined;
const at = (id: string): number => {
  const index = BEATS.findIndex((beat) => beat.id === id);
  if (index === -1) throw new Error(`No beat ${id}.`);
  return index;
};

describe("the demo's beat table", () => {
  it("covers the six chapters in order, from the Discord request to the Case File", () => {
    const order = CHAPTERS.map((chapter) => chapter.id);
    let last = -1;
    for (const beat of BEATS) {
      const chapter = order.indexOf(beat.chapter);
      expect(chapter).toBeGreaterThanOrEqual(last);
      last = chapter;
      if (beat.surface === "session") expect(beat.agentLine).toBeTruthy();
    }
    expect(new Set(BEATS.map((beat) => beat.chapter)).size).toBe(CHAPTERS.length);
    expect(BEATS[0].surface).toBe("discord");
    expect(BEATS[JOIN_INDEX].surface).toBe("session");
    expect(BEATS[BEATS.length - 1].surface).toBe("casefile");
  });

  it("selects beats from the clock and reports progress inside them", () => {
    expect(selectBeat(0)).toBe(0);
    expect(selectBeat(STORY_DURATION)).toBe(BEATS.length - 1);
    expect(beatProgress(BEATS[0].duration / 2, 0)).toBeCloseTo(0.5, 5);
    expect(lastBeatOf("ship")).toBe(BEATS.length - 1);
    // the rail fill follows the chapter, so it never snaps back between an iterate beat and the next
    const repair = at("repair");
    expect(chapterProgress(BEAT_STARTS[repair] - 1, "iterate")).toBeLessThan(chapterProgress(BEAT_STARTS[repair] + 1, "iterate"));
  });
});

describe("the demo page", () => {
  it("opens in Discord with only its tab, and Join interview takes the reader on", () => {
    const onJoin = vi.fn();
    const { rerender } = render(<Story elapsed={BEAT_STARTS[at("invite")] + 1000} active onChapter={noop} onJoin={onJoin} />);
    expect(screen.getByTestId("story-frame")).toHaveAttribute("data-surface", "discord");
    expect(screen.getByTestId("story-tab-discord")).toBeInTheDocument();
    expect(screen.queryByTestId("story-tab-session")).toBeNull();
    // the invitation states the offer
    expect(screen.getByTestId("story-invite-message")).toHaveTextContent("Dudulemon gets $250 off its next bill.");
    fireEvent.click(screen.getByTestId("story-join"));
    expect(onJoin).toHaveBeenCalledTimes(1);
    // the interview opens the session and preview tabs beside Discord
    rerender(<Story elapsed={BEAT_STARTS[JOIN_INDEX]} active onChapter={noop} onJoin={onJoin} />);
    expect(screen.getByTestId("story-frame")).toHaveAttribute("data-surface", "session");
    expect(screen.getByTestId("story-tab-session")).toBeInTheDocument();
    expect(screen.getByTestId("story-tab-preview")).toBeInTheDocument();
  });

  it("keeps one live call in the transcript, including what Maya says in the preview", () => {
    const greet = at("greet");
    const { rerender } = render(<Story elapsed={BEAT_STARTS[greet] + BEATS[greet].duration * 0.5} active onChapter={noop} onJoin={noop} />);
    expect(screen.getByTestId("story-turn-greet-agent")).toHaveTextContent("Wren" + BEATS[greet].agentLine);
    expect(screen.queryByTestId("story-turn-greet-maya")).toBeNull();
    rerender(<Story elapsed={BEAT_STARTS[at("build")] - 1} active onChapter={noop} onJoin={noop} />);
    expect(screen.getAllByTestId(/^story-turn-/).map((turn) => turn.dataset.testid)).toEqual(["story-turn-greet-agent", "story-turn-greet-maya", "story-turn-reset-maya", "story-turn-agree-agent", "story-turn-agree-maya"]);
  });

  it("thanks Maya in Discord with the credit, then completes the last chapter on the Case File", () => {
    const thanks = at("thanks");
    const { rerender } = render(<Story elapsed={BEAT_STARTS[thanks] + BEATS[thanks].duration - 1} active onChapter={noop} onJoin={noop} />);
    expect(screen.getByTestId("story-thanks-message")).toHaveTextContent("Thanks for walking me through your Monday, Maya.");
    expect(screen.getByTestId("story-credit-embed")).toHaveTextContent("Dudulemon gets $250 off its next Shopalytics bill.");
    expect(screen.queryByTestId("story-rail-status")).toBeNull();
    rerender(<Story elapsed={STORY_DURATION} active onChapter={noop} onJoin={noop} />);
    expect(screen.getByTestId("story-page")).toHaveAttribute("data-playback", "finished");
    expect(screen.getByTestId("story-stop-ship")).toHaveAttribute("data-status", "complete");
    expect(screen.getByTestId("story-rail-status")).toHaveTextContent("Complete");
    expect(screen.getByTestId("story-case-closed").textContent).toContain("Case closed");
  });

  it("takes the reader to a chapter from its stop", () => {
    const onChapter = vi.fn();
    render(<Story elapsed={0} active onChapter={onChapter} onJoin={noop} />);
    fireEvent.click(screen.getByTestId("story-jump-validate"));
    expect(onChapter).toHaveBeenCalledWith("validate");
    expect(screen.getByTestId("story-jump-request")).toHaveAccessibleName("Go to the Request chapter");
  });
});

describe("the demo under reduced motion", () => {
  it("does not play, and each chapter stop shows that chapter's finished still", () => {
    const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1440);
    try {
      render(<Demo reducedMotion />);
      expect(screen.getByTestId("landing-demo")).toHaveAttribute("id", "how-it-works");
      expect(screen.queryByTestId("demo-play")).toBeNull();
      expect(screen.getByTestId("story-page")).toHaveAttribute("data-beat", "invite");
      act(() => fireEvent.click(screen.getByTestId("story-jump-ship")));
      expect(screen.getByTestId("story-page")).toHaveAttribute("data-beat", "casefile");
      expect(screen.getByTestId("story-rail")).toHaveAttribute("data-chapter", "ship");
    } finally {
      width.mockRestore();
    }
  });
});

describe("the agent's scene", () => {
  const targets = { bar: { x: 100, y: 100, width: 200, height: 40 } };
  const steps: readonly SceneStep[] = [{ kind: "circle", target: "bar", at: 1000 }, { kind: "label", target: "bar", text: "These seven?", at: 0 }];

  it("glides in, rides the loop it draws, then labels its target", () => {
    expect(sceneAt(steps, targets, 999)).toEqual({ cursor: null, marks: [], labels: [] });
    const gliding = sceneAt(steps, targets, 1100);
    expect(gliding.cursor).not.toBeNull();
    expect(gliding.marks).toEqual([]);
    // the loop starts at the target's right, padded, and the cursor rides where it has drawn to
    const drawing = sceneAt(steps, targets, 1250 + 260);
    expect(drawing.marks).toHaveLength(1);
    expect(drawing.marks[0].drawn).toBeCloseTo(0.5, 1);
    expect(drawing.cursor?.x).toBeLessThan(308);
    const labelled = sceneAt(steps, targets, 3000);
    expect(labelled.marks[0].drawn).toBe(1);
    expect(labelled.labels.map((label) => label.text)).toEqual(["These seven?"]);
    // an unretained mark holds, then fades
    expect(sceneAt(steps, targets, 1250 + 520 + 2500 + 400).marks[0].opacity).toBe(0);
  });

  it("waits for a target that is not registered yet", () => {
    expect(sceneAt(steps, {}, 5000)).toEqual({ cursor: null, marks: [], labels: [] });
  });

  it("moves the customer's pointer from rest to each waypoint", () => {
    const rest = { x: 0, y: 0 };
    expect(pointerAt([{ at: 500, target: "bar" }], targets, rest, 400)).toEqual(rest);
    expect(pointerAt([{ at: 500, target: "bar" }], targets, rest, 2000)).toEqual({ x: 200, y: 120 });
  });
});
