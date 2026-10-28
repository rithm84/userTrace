"use client";

import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import { BEATS, BEAT_STARTS, CHAPTER_STARTS, CHAPTERS, JOIN_INDEX, LOOP_HOLD_MS, PLAYBACK_RATE, STORY_DURATION, lastBeatOf, type ChapterId } from "./beats";
import { PAGE, Story } from "./Story";

/** Room around the page: its rail and caption hang a little past its edges, less so on a phone; above it, the gap under the hero. */
const FIT = Object.freeze({ side: 56, phoneSide: 16, phoneWidth: 760, navH: 72, vertical: 88, maxZoom: 1.25 });
/** The demo plays while at least this much of it is on screen. */
const ON_SCREEN = 0.5;
/** The longest step the clock takes in one frame, so a backgrounded tab resumes where it left off. */
const MAX_STEP_MS = 100;

/** Where a chapter's still sits: the last moment of its last beat. */
function stillOf(id: ChapterId): number {
  const last = lastBeatOf(id);
  return BEAT_STARTS[last] + BEATS[last].duration - 1;
}

/** The demo's clock, in authored ms: it advances at `PLAYBACK_RATE` while `running` and starts over after the finished Case File has held. */
function useDemoClock(running: boolean, initial: number): Readonly<{ elapsed: number; seek: (ms: number) => void }> {
  const [elapsed, setElapsed] = useState(initial);
  useEffect(() => {
    if (!running) return;
    let last = performance.now();
    let frame = requestAnimationFrame(function tick(now: number): void {
      const step = Math.min(MAX_STEP_MS, now - last) * PLAYBACK_RATE;
      last = now;
      setElapsed((value) => (value + step) % (STORY_DURATION + LOOP_HOLD_MS));
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [running]);
  return { elapsed, seek: setElapsed };
}

/**
 * How it works: the product demo right under the hero, played on a clock like a product video
 * rather than by the scroll, so a reader can watch it or scroll straight past. It plays while it
 * is on screen, pauses when it leaves or when the reader pauses it, and loops. The chapter stops
 * jump to a chapter; Join interview jumps to the interview. Under reduced motion it does not
 * play: each chapter stop shows that chapter's finished still.
 */
export function Demo(props: Readonly<{ reducedMotion: boolean }>): JSX.Element {
  const section = useRef<HTMLElement>(null);
  const fit = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  const [visible, setVisible] = useState(false);
  const [paused, setPaused] = useState(false);
  const running = visible && !paused && !props.reducedMotion;
  const { elapsed, seek } = useDemoClock(running, props.reducedMotion ? stillOf(CHAPTERS[0].id) : 0);
  useEffect(() => {
    const element = section.current;
    if (element === null) throw new Error("Cannot fit the demo: its section is missing.");
    const measure = (): void => {
      // a section with no width is not laid out yet
      if (element.clientWidth === 0) return;
      const side = element.clientWidth > FIT.phoneWidth ? FIT.side : FIT.phoneSide;
      const width = (element.clientWidth - side * 2) / PAGE.width;
      const height = (window.innerHeight - FIT.navH - FIT.vertical) / PAGE.height;
      setZoom(Math.min(width, height, FIT.maxZoom));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  const measured = zoom !== null;
  useEffect(() => {
    const element = fit.current;
    if (element === null) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.intersectionRatio >= ON_SCREEN), { threshold: [0, ON_SCREEN, 1] });
    observer.observe(element);
    return () => observer.disconnect();
  }, [measured]);
  const { reducedMotion } = props;
  const onChapter = useCallback((id: ChapterId): void => seek(reducedMotion ? stillOf(id) : CHAPTER_STARTS[CHAPTERS.findIndex((chapter) => chapter.id === id)]), [seek, reducedMotion]);
  const onJoin = useCallback((): void => seek(BEAT_STARTS[JOIN_INDEX]), [seek]);
  return (
    <section ref={section} id="how-it-works" className="st-demo" data-testid="landing-demo" aria-label="How uTrace works, an illustrative demo">
      {zoom !== null ? (
        <div ref={fit} className="st-demo-fit" data-testid="demo-fit" data-playing={running ? "true" : "false"} style={{ width: PAGE.width * zoom, height: PAGE.height * zoom }}>
          <div className="st-demo-zoom" style={{ zoom }}>
            <Story elapsed={elapsed} active={running} onChapter={onChapter} onJoin={onJoin} />
            {reducedMotion ? null : (
              <button type="button" className="st-play" data-testid="demo-play" aria-label={paused ? "Play the demo" : "Pause the demo"} onClick={() => setPaused((value) => !value)}>
                {paused ? <Play size={16} strokeWidth={2.6} aria-hidden="true" /> : <Pause size={16} strokeWidth={2.6} aria-hidden="true" />}
              </button>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
