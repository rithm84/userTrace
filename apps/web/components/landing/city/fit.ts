/**
 * How the 2400 x 1000 design box sits in the hero. The fit shows `span` design pixels across the
 * stage and hangs the city from the copy: `COPY_LINE` (the highest the city rises under the
 * waitlist bar) lands a small gap below the bar, so the city always starts right where the copy
 * ends. When that would leave the stage's bottom uncovered, the city first grows (a little on
 * desktop, more on a phone, where the centre of the city is all that fits), then drops to the
 * bottom; when the band under the copy is too thin, the city shrinks.
 */
export const DESIGN = Object.freeze({ width: 2400, height: 1000, centre: 1200, span: 1900 });
/** The design line the copy's last element (the waitlist bar) must clear. */
const COPY_LINE = 612;
/**
 * The copy's footprint in design pixels at a typical laptop width: within `dx` of the centre line,
 * nothing in the city may rise above `y` (the waitlist bar, the lede and the two headline lines).
 * Beyond the widest zone the side towers stand beside the copy.
 */
export const KEEP_OUT: readonly Readonly<{ dx: number; y: number }>[] = [
  { dx: 440, y: COPY_LINE },
  { dx: 650, y: COPY_LINE - 135 },
  { dx: 600, y: COPY_LINE - 250 },
  { dx: 480, y: COPY_LINE - 404 },
];
/** How far down the design the city's ground reliably reaches, and the least of it worth showing. */
const GROUND_DEPTH = 1180;
const MIN_BAND = 190;
/** Stage pixels between the waitlist bar and the highest thing under it. */
const GAP = 26;
/** Stage pixels of clear paper the city's highest visible point keeps under the navbar. */
export const NAV_CLEAR = 32;

/**
 * Where the city sits (`scale`, `left`, `top`) and how far the whole hero is pushed down (`push`,
 * stage pixels added above the copy). The city hangs from the waitlist bar; when its highest
 * visible point would come within `NAV_CLEAR` of the navbar, copy and city move down together, by
 * the least push that clears it.
 */
export type CityFit = Readonly<{ scale: number; left: number; top: number; push: number }>;

/**
 * `copyBottom` is the waitlist bar's bottom edge with no push, in stage pixels from the stage's
 * top; `skyline(from, to)` is the least design y the city reaches between two design x.
 */
export function cityFit(stageWidth: number, stageHeight: number, copyBottom: number, skyline: (from: number, to: number) => number): CityFit {
  if (!(stageWidth > 0) || !(stageHeight > 0)) throw new RangeError(`The city needs a measured stage; got ${stageWidth} x ${stageHeight}.`);
  const clearance = (fit: Hang): number => fit.top + skyline(-fit.left / fit.scale, (stageWidth - fit.left) / fit.scale) * fit.scale;
  const at = (push: number): Hang => hang(stageWidth, stageHeight, copyBottom + push);
  if (clearance(at(0)) >= NAV_CLEAR) return { ...at(0), push: 0 };
  // pushing moves the city down with the bar, so the clearance grows with the push: bisect for the least that clears
  let [low, high] = [0, stageHeight];
  if (clearance(at(high)) < NAV_CLEAR) throw new RangeError(`The city cannot clear the navbar on a ${stageWidth} x ${stageHeight} stage, even pushed down by the stage's height.`);
  for (let i = 0; i < 24; i += 1) {
    const mid = (low + high) / 2;
    if (clearance(at(mid)) >= NAV_CLEAR) high = mid;
    else low = mid;
  }
  return { ...at(high), push: high };
}

type Hang = Readonly<{ scale: number; left: number; top: number }>;

function hang(stageWidth: number, stageHeight: number, copyBottom: number): Hang {
  const byWidth = stageWidth >= 900 ? stageWidth / DESIGN.span : Math.min(0.5, Math.max(0.36, stageWidth / 960));
  const room = stageHeight - copyBottom - GAP;
  const filling = room / (GROUND_DEPTH - COPY_LINE);
  const grown = Math.min(filling, byWidth * (stageWidth < 900 ? 1.6 : 1.15));
  const scale = room >= MIN_BAND * byWidth ? Math.max(byWidth, grown) : Math.max(0.2, room / MIN_BAND);
  const hung = copyBottom + GAP - COPY_LINE * scale;
  const top = hung + GROUND_DEPTH * scale < stageHeight ? stageHeight - GROUND_DEPTH * scale : hung;
  return { scale, left: stageWidth / 2 - DESIGN.centre * scale, top };
}

