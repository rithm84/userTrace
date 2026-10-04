/**
 * The door's three honest failure surfaces.
 *
 * They are written in the uTrace register from `docs/DESIGN.md` — ink on paper, one
 * coral accent, square corners, a hard ink shadow, sentence case, plain sentences — and
 * deliberately without comic devices: no captions, stickers, balloons, stamps or
 * lettering, and no character rigs. These pages meet a participant at a moment where
 * something has gone wrong or nothing is there, so the page says only what is true.
 *
 * The neutral page carries no authority: it does not confirm that a session exists, name
 * a client, or hint that a different link would work. Every page is static text with no
 * interpolated values, so no request data can reach the markup, and no font, script, or
 * image is fetched.
 */

export type InterstitialKind =
  | "neutral"
  | "ended"
  | "reconnecting"
  | "unavailable";

interface InterstitialCopy {
  readonly title: string;
  readonly heading: string;
  readonly lede: string;
  readonly detail: string;
  readonly refreshSeconds: number | null;
}

const copy: Record<InterstitialKind, InterstitialCopy> = {
  neutral: {
    title: "Nothing to open here",
    heading: "Nothing to open here",
    lede: "This address does not open a uTrace preview.",
    detail:
      "Preview addresses are private to one session and work only from the session that created them.",
    refreshSeconds: null,
  },
  ended: {
    title: "This preview has ended",
    heading: "This preview has ended",
    lede: "The session that used this address is closed.",
    detail:
      "The product environment behind it has been shut down. If uTrace needs you again, it will reach you where your invitation arrived.",
    refreshSeconds: null,
  },
  reconnecting: {
    title: "Reconnecting to your preview",
    heading: "Reconnecting to your preview",
    lede: "The product environment for this session is coming back.",
    detail:
      "Your session is still open and nothing you did caused this. This page tries again on its own every few seconds.",
    refreshSeconds: 5,
  },
  // A fourth surface the component doc does not name. The three named pages each assert
  // something — nothing here, the session is over, the runtime is coming back — and none
  // of those is true when uTrace itself fails, so this page asserts nothing instead.
  unavailable: {
    title: "This preview is not reachable right now",
    heading: "This preview is not reachable right now",
    lede: "uTrace cannot reach the preview for this address at the moment.",
    detail:
      "This is a fault on the uTrace side, not something you did, and it says nothing about whether your session is still open. The failure is recorded. Try again in a moment.",
    refreshSeconds: null,
  },
};

const styles = `
  :root {
    --ink: #000000;
    --ink-2: #3d3d3d;
    --ink-3: #7a7a7a;
    --paper: #ffffff;
    --rule: #e6e6e6;
    --coral: #fcada3;
    --coral-ink: #a63a2e;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    padding: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    background-color: var(--paper);
    background-image: radial-gradient(#c9c9c9 0.95px, transparent 1.05px);
    background-size: 6px 6px;
    color: var(--ink);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 17px;
    line-height: 1.5;
  }
  main {
    width: min(560px, 100%);
    padding: 32px;
    background: var(--paper);
    border: 3px solid var(--ink);
    box-shadow: 6px 6px 0 var(--ink);
  }
  h1 {
    margin: 0;
    font-size: 32px;
    font-weight: 800;
    letter-spacing: -0.02em;
    line-height: 1.15;
  }
  .accent {
    width: 64px;
    height: 6px;
    margin: 20px 0;
    background: var(--coral);
  }
  p { margin: 0 0 12px; }
  p.detail { margin-bottom: 0; color: var(--ink-2); }
  footer {
    margin-top: 28px;
    padding-top: 14px;
    border-top: 1px solid var(--rule);
    font-size: 14px;
    color: var(--ink-3);
  }
  footer b { color: var(--coral-ink); font-weight: 700; }
`;

/** Render one interstitial as a complete, self-contained HTML document. */
export function renderInterstitial(kind: InterstitialKind): string {
  const page = copy[kind];
  const refresh =
    page.refreshSeconds === null
      ? ""
      : `\n    <meta http-equiv="refresh" content="${page.refreshSeconds}">`;
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">${refresh}
    <title>${page.title}</title>
    <style>${styles}</style>
  </head>
  <body>
    <main>
      <h1>${page.heading}</h1>
      <div class="accent"></div>
      <p>${page.lede}</p>
      <p class="detail">${page.detail}</p>
      <footer><b>uTrace</b> preview</footer>
    </main>
  </body>
</html>
`;
}
