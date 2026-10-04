import { UTRACE_LOGO_GLASS_PATH } from "@/components/brand/logoPaths";
import { WORDMARK } from "@/components/brand/wordmarkPath";

type Tone = "dark" | "light";

/**
 * The uTrace mark (a magnifying glass whose lens holds two tall oval eyes)
 * next to the wordmark, the display face at weight 800 baked to a path so it
 * draws identically at every size (live text notched the T at small sizes).
 * The wordmark's font-size class still sets its height, and its colour class
 * its fill.
 */
export function Logo({
  tone = "dark",
  size = 28,
  withWordmark = true,
  wordmarkClassName = "",
}: {
  tone?: Tone;
  size?: number;
  withWordmark?: boolean;
  wordmarkClassName?: string;
}) {
  const glassFill = tone === "light" ? "#ffffff" : "#000000";

  return (
    <span className="inline-flex items-center gap-2.5 align-middle">
      <span
        aria-hidden
        style={{
          width: size,
          height: size,
          display: "inline-block",
          lineHeight: 0,
        }}
        className="[&_svg]:block [&_svg]:h-full [&_svg]:w-full"
      >
        <svg viewBox="0 0 1024 1024" width={size} height={size} aria-label="uTrace logo" role="img">
          <path
            d={UTRACE_LOGO_GLASS_PATH}
            fill={glassFill}
            fillRule="evenodd"
            clipRule="evenodd"
          />
        </svg>
      </span>
      {withWordmark ? (
        <span className={wordmarkClassName} style={{ display: "inline-block", lineHeight: 0 }}>
          <svg
            viewBox={`0 ${-WORDMARK.ascent} ${WORDMARK.advance} ${WORDMARK.ascent + WORDMARK.descent}`}
            style={{ display: "block", height: `${(WORDMARK.ascent + WORDMARK.descent) / 1000}em`, width: "auto" }}
            role="img"
            aria-label="uTrace"
          >
            <path d={WORDMARK.d} fill="currentColor" />
          </svg>
        </span>
      ) : null}
    </span>
  );
}
