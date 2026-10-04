'use client';

import { animate, motion, useMotionValue, useMotionValueEvent, useScroll, useTransform, type MotionValue } from 'motion/react';
import { memo, useCallback, useEffect, useRef, useState, type JSX } from 'react';
import { preload } from 'react-dom';
import { useReducedMotionPreference } from '@/components/agent/useReducedMotion';
import { CITY_RIG_URLS } from '../city/cast';
import { Hero } from '../hero/Hero';
import { CHAPTER_STARTS, CHAPTERS, STORY_DURATION, STORY_SEGMENTS, type ChapterId } from '../story/beats';
import { Story } from '../story/Story';
import { Strip } from '../strip/Strip';
import { STRIP_SEGMENTS } from '../strip/cues';
import { PAGE, pageFit, panel, panelBox } from '../strip/stripGeometry';
import { clamp01, JOURNEY, smooth, STORY_SCROLL_END, STORY_SCROLL_START, STRIP_SCROLL_END, STRIP_STAGE, type StageSize } from './contract';
import { landingTop } from './landingEntry';
import { JourneyAside, SkipAhead, StripProgress } from './JourneyAside';
import { scrollAtTime, timeAtScroll } from './scrollTimeline';
import { useScrollTimeline } from './useScrollTimeline';
import './journey.css';

const PRELOADED = ['/agent/rive.wasm', ...CITY_RIG_URLS] as const;
const CLICK = panelBox('click');
/**
 * The zoom's first stretch is the page alone, scaling about its last panel; then the page dissolves
 * while the ink frame (the same panel outline, on the same path) and the story fade in behind it.
 * A reader who stops just past the strip's end sees no ghost of the story in the last panel.
 */
const ZOOM_FADE = Object.freeze({ stripOut: [0.1, 0.4], storyIn: [0.15, 0.45], frameIn: [0.12, 0.3] });

/** The zoom that takes the page into the story: the last panel's centre and how far it grows to fill the stage. */
function panelZoom(size: StageSize): Readonly<{ centre: Readonly<{ x: number; y: number }>; grow: number }> {
  const fit = pageFit(size.width, size.height);
  return { centre: { x: CLICK.left + CLICK.width / 2, y: CLICK.top + CLICK.height / 2 }, grow: Math.max(size.width / (CLICK.width * fit.scale), size.height / (CLICK.height * fit.scale)) };
}

/** Where a point of the page (in page units) sits on the stage at zoom `t`: exactly where the zooming page puts it. */
function zoomedPoint(point: readonly [number, number], t: number, size: StageSize): readonly [number, number] {
  const fit = pageFit(size.width, size.height);
  const { centre, grow } = panelZoom(size);
  const s = smooth(t);
  const k = 1 + (grow - 1) * s;
  const x = centre.x + (point[0] - centre.x) * k + (PAGE.width / 2 - centre.x) * s;
  const y = centre.y + (point[1] - centre.y) * k + (PAGE.height / 2 - centre.y) * s;
  return [fit.x + x * fit.scale, fit.y + y * fit.scale];
}
/** Past this width the page is too small to scroll through: phones get the finished strip and the story's stills in normal flow. */
const PHONE_QUERY = '(max-width: 760px)';
/** Input that means the reader is steering the page, which the Join interview scroll must yield to. */
const READER_INPUT = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/**
 * Scroll drives the journey. It lifts the hero off the strip like a sheet of paper, then plays
 * the strip, whose timeline follows the scroll (`useScrollTimeline`); the scroll itself presses
 * Join interview, whose button stays a shortcut into the story until then. Further scroll zooms
 * from the last panel into the story and plays it the same way. Scroll position chooses the
 * panel or beat, so scrolling back returns to earlier ones and a reload keeps the reader's place,
 * but what the reader has watched is never played backwards. Beside either
 * page, Skip ahead leaves the journey; the story's chapter stops jump to their chapters.
 * On phones and under reduced motion the journey is stacked instead: the finished strip and the
 * story's stills in normal flow.
 * The hero is gated on its own parts (the city's rigs) so it never appears in pieces, and
 * everything beneath it mounts only after that, so the hero's assets load first.
 */
export function LandingJourney(): JSX.Element {
  const sectionRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const heroRef = useRef<HTMLDivElement>(null);
  const restored = useRef(false);
  const joinAnimation = useRef<Readonly<{ stop: () => void }> | null>(null);
  const preference = useReducedMotionPreference();
  const phone = usePhoneLayout();
  const resolved = preference.resolved && phone.resolved;
  const reducedMotion = preference.resolved && preference.reducedMotion;
  const stacked = resolved && (reducedMotion || phone.phone);
  const lift = useMotionValue(0);
  const stripTime = useMotionValue(0);
  const stripScroll = useMotionValue(0);
  const transition = useMotionValue(0);
  const storyTime = useMotionValue(0);
  const storyScroll = useMotionValue(0);
  const [visible, setVisible] = useState(false);
  const [heroOnScreen, setHeroOnScreen] = useState(false);
  const [size, setSize] = useState<StageSize | null>(null);
  const [lifted, setLifted] = useState(false);
  const [zooming, setZooming] = useState(false);
  const [pastStrip, setPastStrip] = useState(false);
  const [storyReady, setStoryReady] = useState(false);
  const [heroReady, setHeroReady] = useState(false);
  const onHeroReady = useCallback((): void => setHeroReady(true), []);
  // the hero appears as a whole: copy and city together, once every rig in it has drawn
  const stageReady = reducedMotion || heroReady;
  for (const href of PRELOADED) preload(href, { as: 'fetch', crossOrigin: 'anonymous' });
  const { scrollY } = useScroll();
  useMotionValueEvent(lift, 'change', (p) => setLifted(p >= STRIP_STAGE.lifted));
  useMotionValueEvent(transition, 'change', (p) => { setZooming(p > 0.02); setPastStrip(p > STRIP_STAGE.transition); setStoryReady(p >= 0.995); });
  useEffect(() => {
    const el = stageRef.current;
    if (el === null) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: stacked ? window.innerHeight - JOURNEY.navH : entry.contentRect.height });
    });
    observer.observe(el);
    const visibility = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting && entry.intersectionRatio > 0.65), { threshold: [0, 0.65, 1] });
    visibility.observe(el);
    return () => { observer.disconnect(); visibility.disconnect(); };
  }, [stacked]);
  useEffect(() => {
    // stacked, the hero scrolls away like any section, so its city runs only while it is on screen
    const hero = heroRef.current;
    if (!stacked || hero === null) return;
    const onScreen = new IntersectionObserver(([entry]) => setHeroOnScreen(entry.isIntersecting));
    onScreen.observe(hero);
    return () => onScreen.disconnect();
  }, [stacked]);
  const armed = !stacked && lifted && visible && !pastStrip;
  // both pages follow their scroll position, so a jump past either one simply lands there, and
  // neither rewinds what the reader watched: the strip is watched from the lift until the story
  // covers it, the story once the zoom has opened it
  const stripElapsed = useScrollTimeline(stripTime, STRIP_SEGMENTS, !stacked && lifted && visible && !storyReady, stacked);
  const storyElapsed = useScrollTimeline(storyTime, STORY_SEGMENTS, !stacked && storyReady && visible, stacked);
  useEffect(() => {
    // the Join interview scroll yields the moment the reader steers the page themselves
    const yieldToReader = (): void => joinAnimation.current?.stop();
    for (const type of READER_INPUT) window.addEventListener(type, yieldToReader, { capture: true, passive: true });
    return () => {
      for (const type of READER_INPUT) window.removeEventListener(type, yieldToReader, { capture: true });
      joinAnimation.current?.stop();
    };
  }, []);
  const measured = size !== null;
  useEffect(() => {
    // the pages mount only once the hero is ready, and a stacked restore measures them
    if (!resolved || !measured || !stageReady) return;
    const section = sectionRef.current;
    if (section === null) throw new Error('Cannot restore the landing page: the journey section is missing.');
    if (!restored.current) {
      restored.current = true;
      window.scrollTo({ top: landingTop(section, stacked), behavior: 'instant' });
    }
    if (stacked) return;
    const sync = (y: number): void => {
      const section = sectionRef.current;
      if (section === null) return;
      const distance = y - section.offsetTop + JOURNEY.navH;
      lift.set(clamp01(distance / JOURNEY.heroPx));
      stripTime.set(timeAtScroll(distance - JOURNEY.heroPx, STRIP_SEGMENTS));
      stripScroll.set(Math.min(STRIP_SCROLL_END, Math.max(JOURNEY.heroPx, distance)) - JOURNEY.heroPx);
      transition.set(clamp01((distance - STRIP_SCROLL_END) / JOURNEY.transitionPx));
      storyTime.set(timeAtScroll(distance - STORY_SCROLL_START, STORY_SEGMENTS));
      storyScroll.set(Math.min(STORY_SCROLL_END, Math.max(STORY_SCROLL_START, distance)) - STORY_SCROLL_START);
    };
    sync(window.scrollY);
    return scrollY.on('change', sync);
  }, [lift, stripTime, stripScroll, transition, storyTime, storyScroll, scrollY, resolved, stacked, measured, stageReady]);

  // the cast keeps moving through the zoom and parks only once the strip layer has gone
  const live = armed || (zooming && !storyReady);
  const join = useCallback((): void => {
    const section = sectionRef.current;
    if (section === null) throw new Error('Cannot join the interview: the journey section is missing.');
    const target = section.offsetTop - JOURNEY.navH + STORY_SCROLL_START;
    joinAnimation.current = animate(window.scrollY, target, { duration: 1.25, ease: [0.7, 0, 0.25, 1], onUpdate: (value) => window.scrollTo(0, value) });
  }, []);
  const goToChapter = useCallback((id: ChapterId): void => {
    const section = sectionRef.current;
    if (section === null) throw new Error('Cannot open the story chapter: the journey section is missing.');
    const start = CHAPTER_STARTS[CHAPTERS.findIndex((chapter) => chapter.id === id)];
    window.scrollTo({ top: section.offsetTop - JOURNEY.navH + STORY_SCROLL_START + scrollAtTime(start, STORY_SEGMENTS), behavior: 'instant' });
  }, []);
  const asideLeft = size === null ? null : asideSpot(size);
  const staticStrip: StageSize | null = size === null ? null : { width: size.width, height: PAGE.height * pageFit(size.width, size.height).scale + 32 };

  return <section ref={sectionRef} id="landing-journey" data-testid="landing-journey" data-layout={stacked ? 'stacked' : 'scrolled'} className={'v3-journey' + (stacked ? ' v3-stacked' : '')} style={{ height: stacked ? 'auto' : 'calc(100svh - 72px + ' + (STORY_SCROLL_END + JOURNEY.storyHoldPx) + 'px)' }}>
    {!stacked ? <span id="how-it-works" className="v3-story-anchor" style={{ top: JOURNEY.heroPx }} /> : null}
    <div ref={stageRef} data-testid="journey-stage" className="v3-stage">
      {stacked
        ? reducedMotion
          ? <Hero live={false} still onReady={onHeroReady} />
          : <div ref={heroRef} className={'v3-reveal' + (heroReady ? ' is-ready' : '')}><Hero live={heroOnScreen && heroReady} still={false} onReady={onHeroReady} /></div>
        : <HeroLayer lift={lift} ready={stageReady} live={visible && !lifted} onReady={onHeroReady} />}
      {size !== null && staticStrip !== null && stageReady ? <>
        {stacked
          ? <div className="v3-static">
            {/* the strip fits its own box, not the stage: fitted to the stage it would sit centred on a screen-tall box and slide over the stills */}
            <div id="how-it-works" className="v3-static-strip" style={{ scrollMarginTop: JOURNEY.navH, height: staticStrip.height }}><StripLayer size={staticStrip} transition={transition} elapsed={stripElapsed} live reducedMotion onJoin={join} /></div>
            {/* the stills are the story's 1200px pages, fitted to the width the way the strip is */}
            <div className="v3-static-story" style={{ zoom: Math.min(1, (size.width - 32) / PAGE.width) }}><Story elapsed={STORY_DURATION} active={visible} scroll={null} onChapter={null} reducedMotion /></div>
          </div>
          : <>
            <StripLayer size={size} transition={transition} elapsed={stripElapsed} live={live} reducedMotion={false} onJoin={join} />
            <StoryLayer size={size} transition={transition} elapsed={storyElapsed} active={storyReady && visible} scroll={storyScroll} onChapter={goToChapter} />
            {armed && !zooming ? <JourneyAside left={asideLeft}><StripProgress scroll={stripScroll} drawn={stripElapsed} /><SkipAhead /></JourneyAside> : null}
            {storyReady && visible ? <JourneyAside left={asideLeft}><SkipAhead /></JourneyAside> : null}
          </>}
      </> : null}
    </div>
  </section>;
}

/** Whether the viewport is phone-sized, resolved after mount so the server render never guesses. */
function usePhoneLayout(): Readonly<{ phone: boolean; resolved: boolean }> {
  const [state, setState] = useState<Readonly<{ phone: boolean; resolved: boolean }>>({ phone: false, resolved: false });
  useEffect(() => {
    const media = window.matchMedia(PHONE_QUERY);
    const update = (): void => setState((current) => current.resolved && current.phone === media.matches ? current : { phone: media.matches, resolved: true });
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return state;
}

type LayerProps = Readonly<{ size: StageSize; transition: MotionValue<number>; elapsed: number; live: boolean; reducedMotion: boolean; onJoin: () => void }>;

/**
 * The page fitted to the stage; the zoom scales it about the last panel's centre as the story
 * takes over. The page dissolves early in the zoom (after the frame alone has begun to grow):
 * what carries the reader in is the story frame growing out of the last panel, not a page of
 * crops and live rigs blown up three times.
 */
const StripLayer = memo(function StripLayer(props: LayerProps): JSX.Element {
  const { size, transition } = props;
  const fit = pageFit(size.width, size.height);
  const { centre, grow } = panelZoom(size);
  const zoom = useTransform(transition, (t) => `translate(${((PAGE.width / 2 - centre.x) * smooth(t)).toFixed(2)}px, ${((PAGE.height / 2 - centre.y) * smooth(t)).toFixed(2)}px) scale(${(1 + (grow - 1) * smooth(t)).toFixed(4)})`);
  const fade = useTransform(transition, ZOOM_FADE.stripOut, [1, 0]);
  const [hidden, setHidden] = useState(transition.get() >= 0.995);
  useMotionValueEvent(transition, 'change', (t) => setHidden(t >= 0.995));
  return <motion.div className="v3-strip-layer" data-testid="strip-layer" inert={hidden} aria-hidden={hidden}>
    <div className="v3-strip-fit" style={{ left: fit.x, top: fit.y, width: PAGE.width * fit.scale, height: PAGE.height * fit.scale }}>
      <div className="v3-strip-zoomed" style={{ zoom: fit.scale }}>
        <motion.div className="v3-strip-zoom" style={{ transformOrigin: `${centre.x}px ${centre.y}px`, transform: props.reducedMotion ? 'none' : zoom, opacity: props.reducedMotion ? 1 : fade }}>
          <Strip elapsed={props.elapsed} live={props.live} reducedMotion={props.reducedMotion} onJoin={props.onJoin} />
        </motion.div>
      </div>
    </div>
  </motion.div>;
});

/**
 * The story clipped to the last panel's own quadrilateral, slanted gutter and all, riding the
 * exact zoom the page makes, so its ink frame lies on the panel's own border rather than beside
 * it: the panel opens out, it is not replaced by a rectangle.
 */
const StoryLayer = memo(function StoryLayer(props: Readonly<{ size: StageSize; transition: MotionValue<number>; elapsed: number; active: boolean; scroll: MotionValue<number>; onChapter: (id: ChapterId) => void }>): JSX.Element {
  const { size, transition } = props;
  const fit = pageFit(size.width, size.height);
  const corners = (t: number): readonly (readonly [number, number])[] => panel('click').points.map((point) => zoomedPoint(point, t, size));
  const clip = useTransform(transition, (t) => `polygon(${corners(t).map(([x, y]) => `${x.toFixed(1)}px ${y.toFixed(1)}px`).join(', ')})`);
  const points = useTransform(transition, (t) => corners(t).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' '));
  const frameRef = useRef<SVGPolygonElement>(null);
  useEffect(() => {
    const draw = (value: string): void => frameRef.current?.setAttribute('points', value);
    draw(points.get());
    return points.on('change', draw);
  }, [points]);
  const frameOpacity = useTransform(transition, [ZOOM_FADE.frameIn[0], ZOOM_FADE.frameIn[1], 0.9, 1], [0, 1, 1, 0]);
  const opacity = useTransform(transition, ZOOM_FADE.storyIn, [0, 1]);
  const [ready, setReady] = useState(transition.get() >= 0.995);
  useMotionValueEvent(transition, 'change', (t) => setReady(t >= 0.995));
  return <>
    <motion.div className="v3-story-layer" data-testid="story-layer" style={{ clipPath: clip, opacity }} inert={!ready} aria-hidden={!ready}>
      <div className="v3-strip-fit" style={{ left: fit.x, top: fit.y, width: PAGE.width * fit.scale, height: PAGE.height * fit.scale }}>
        <div className="v3-strip-zoomed" style={{ zoom: fit.scale }}>
          <Story elapsed={props.elapsed} active={props.active} scroll={props.scroll} onChapter={props.onChapter} reducedMotion={false} />
        </div>
      </div>
    </motion.div>
    <motion.svg className="v3-zoom-frame" aria-hidden="true" width={size.width} height={size.height} viewBox={`0 0 ${size.width} ${size.height}`} style={{ opacity: frameOpacity }}><polygon ref={frameRef} /></motion.svg>
  </>;
});

/** The hero is a sheet over the strip: scrolling lifts it straight up, its ink edge showing once it moves. */
const HeroLayer = memo(function HeroLayer(props: Readonly<{ lift: MotionValue<number>; ready: boolean; live: boolean; onReady: () => void }>): JSX.Element {
  const y = useTransform(props.lift, (p) => `${(-100 * p).toFixed(3)}%`);
  const edge = useTransform(props.lift, (p) => (p > 0 ? '0 3px 0 #000' : 'none'));
  const [hidden, setHidden] = useState(props.lift.get() >= 1);
  useMotionValueEvent(props.lift, 'change', (p) => setHidden(p >= 1));
  return <motion.div className="v3-hero-layer" style={{ y, boxShadow: edge }} inert={hidden} aria-hidden={hidden}><div className={'v3-reveal' + (props.ready ? ' is-ready' : '')} style={{ height: '100%' }}><Hero live={props.live && props.ready && !hidden} still={false} onReady={props.onReady} /></div></motion.div>;
});

/** Where the column beside a page stands: in the stage's right margin, or null when the page leaves no room and it takes the corner. */
function asideSpot(size: StageSize): number | null {
  const fit = pageFit(size.width, size.height);
  const right = fit.x + PAGE.width * fit.scale;
  return size.width - right >= 108 ? right + 12 : null;
}
