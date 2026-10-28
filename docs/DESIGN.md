# uTrace Design System

The visual language of utrace.dev and of the product surfaces the landing shows
(the Interview Session tab, the preview extension, the implementation workspace,
the Case File). It describes the system as built in `apps/web/components/landing/`
and in `packages/utrace-agent-character/`. Read it before any design work.

## 1. The register

uTrace is a friendly detective. A customer's problem is a case; the agents follow
it from the first complaint to the merged change; the last frame says "Case
closed!!!". The page is a comic page: white paper, black ink, one coral, panels cut
by leaning gutters, and a cast of seven characters who do the work on the frame.

Three rules hold everything together:

1. **Comic devices stay outside the product frame; real UI stays inside it.**
   Captions, stickers, balloons, stamps and lettering live on the page around a
   window or a crop. Inside a window is the product, drawn as a real DOM component
   at its own type and palette. Nothing inside a frame is a picture. The only
   drawn art on the page is the hero's city and the magnifying glass in the
   closing.
2. **One device, once.** A page gets one balloon, one burst, one stamp, one shout.
   Repetition turns the register into a costume.
3. **Nothing is faked.** A crop of Discord reproduces Discord; a dashboard is a
   working layout with real numbers from the shared fixture; a button that looks
   pressable is a button. The waitlist email fields validate locally and show a
   prototype confirmation shared across both forms for the current visit;
   they do not store or send the email yet (`landing/Waitlist.tsx`). Book a demo and Contact
   are email links to rithm84@utrace.dev.

## 2. Tokens

Defined once on `[data-landing]` in `landing/landing.css`.

| Role | Token | Value |
|---|---|---|
| Ink | `--ink` | `#000000` |
| Secondary ink | `--ink-2` / `--ink-3` | `#3d3d3d` / `#7a7a7a` |
| Paper | `--paper` / `--paper-2` | `#ffffff` / `#f6f6f6` |
| Rules | `--rule` / `--rule-2` | `#e6e6e6` / `#d4d4d4` |
| Coral | `--coral` | `#fcada3` |
| Coral, strong | `--coral-strong` | `#fa8d82` |
| Coral, ink | `--coral-ink` | `#a63a2e` |
| Coral, tint | `--coral-tint` | `#ffd3cc` |
| Cast | `--coral --char-black --mint --sky --lilac --peach --aqua` | see section 3 |

Coral is the only accent. Everything selected, stamped, pressed or emphasised is
coral, coral-strong or coral-tint; the cast colours belong to the characters and
to nothing else, with one exception: the hero's city paints its buildings in the
cast pastels (section 5a).

**Type.** Display is Bricolage Grotesque at weight 800 with tight tracking
(`--display`); body is Satoshi (`--body`); Bangers (`--sfx`) is comic lettering
and appears in exactly two places, the hero's rotating word and the one shout.
Section headings are 54 to 56px; the lede is 17 to 20px regular; captions and
labels are 13 to 15px bold; product UI inside a frame uses that product's scale.
Everything is sentence case. No thin, letter-spaced, uppercase or mono labels.

**Ink and depth.** Panels carry a 3px ink border; components inside them 2px;
hairlines are 1px `--rule`. Depth is a hard offset shadow in ink, never a blur:
3px on a sticker or tile, 4px on a bay or divider, 6px on a crop, 8px on the
browser frame, 10px on the folder. Corners are square on panels, buttons and
sheets; 10px on the platform tiles; round on stickers.

**Ground.** A comic page sits on a dot screen: `radial-gradient(#c9c9c9 0.95px,
transparent 1.05px)` on a 6px grid. Everything else sits on plain paper.

**Easing.** `--ease-out`, `--ease-smooth`, `--ease-spring`, `--ease-quint` for
the few things that move continuously; comic motion uses `steps()` instead.

## 3. The cast

Seven agents, one body shape and colour each, the same face: two black oval eyes
(white on the black body) that carry every expression.

| Colour | Hex | Job on the page |
|---|---|---|
| coral | `#fcada3` | investigates at the city's feedback tower; is Wren, the one product agent in every tab of the demo, and leads its Request chapter on the rail; hangs onto the magnifier's handle in the closing |
| black | `#111111` | builds at the city's site and hands the build to mint; leads the demo's Build chapter on the rail; peeks over the Case File folder; pulls on the magnifier's rim |
| mint | `#97d6a4` | ships, riding the build to the city's shop on the delivery trike; leads the demo's Ship chapter on the rail; peeks over the folder; pulls on the rim |
| sky | `#85cef9` | interviews at the city's café; leads the demo's Interview chapter on the rail; investigates on the case board; pulls on the rim |
| lilac | `#cdb6f9` | validates at the city's shop; leads the demo's Iterate chapter on the rail; listens to the capability request; pulls on the rim |
| peach | `#f5b488` | leads the demo's Validate chapter on the rail; peeks over the folder; pulls on the rim |
| aqua | `#72d8d7` | climbs out of the city's manhole with the pain point; concern on the case board's workaround case; pulls on the rim |

Every character is a Rive rig from `packages/utrace-agent-character/assets/`
(`utrace-agent-<colour>.riv`, artboard `AgentCharacter`, state machine
`AgentCharacterMachine`). The rig exposes one fourteen-value `state` enum
(`idle, investigating, listening, thinking, speaking, acknowledgement, concern,
building, completion, ship, reconnecting, paused, sleep, pointer`) plus `gazeX`,
`gazeY`, `speechEnergy` and `motionScale`. The landing adds three landing-only
rigs (`utrace-landing-<coral|black|mint>.riv`, played by `LandingRig`) built by
the same tooling. Build scripts, the character vectors and the motion rules live
in `packages/utrace-agent-character/rive/`.

Rules for placing a character:

- One specialist per job. A chapter, a panel or a case gets one character, and
  the rail and the sticker show the same character in the same state at the
  same moment. Inside the demo's product screens the agent is always one
  agent, as the product presents it: Wren, drawn coral, in the state of the
  chapter's specialist on the rail.
- A character sits in a container: a round sticker, a rounded sticker, a
  jagged burst, a folder edge, a chapter stop. Containers fade in; they never
  scale, because a rig inside a scaling box measures its canvas small and stays
  blurry. Rig canvases are re-sized only when stale (`characters/rigCanvas.ts`).
- Rigs stay live while the demo plays on screen, and park only after their
  entrance has played (`ENTRANCE_MS`).
- Containers never cover a colourful or load-bearing part of a crop: a logo, a
  card's title, a button.
- Eyes follow the reader where a pointer makes sense (`usePointerGaze`), and
  follow the thing they are talking about everywhere else.
- Some rig states are one-way: `ship` dashes off and leaves an empty canvas, so
  the rail never plays it through. The Ship stop freezes the state on its opening
  pose, satchel in hand (`park`), so its badge is never empty.

## 4. Comic devices

- **Panels.** A page is a fixed design box (the demo 1200 x 800, the case board
  1180 x 520) fitted to its stage with CSS `zoom`, never a transform, so text
  and canvases rasterise at the displayed size. The case board's panels are
  quadrilaterals with gutters that lean 36px over the page height and stay 12px
  wide; the clip and the 3px stroke read from one geometry file so they never
  disagree (`helps/helpsGeometry.ts`). Borders draw on.
- **Captions.** A white box with a 2px ink border and bold 13 to 15px text,
  pinned to a panel corner, popping on two frames. A caption is a plain sentence
  that adds something the panel does not already say; never "x, y" fragments,
  never a repeat of on-screen text.
- **Stickers.** A character in a round badge (2.5px ink, 3px hard shadow)
  overhanging a panel corner or a gutter.
- **Stamp and shout.** Each once: the rotated "Validated" on the Case File;
  "Case closed!!!" in Bangers with an ink stroke and jittering marks as the
  demo's last beat.
- **The magnifier.** One on the page, in the closing: a drawn glass (black ink,
  curved hatching, a dot band inside the rim, an alpha WebP under
  `public/landing/closing/`) in a tug of war. Coral hangs onto the handle on
  the left; the other six hang off the right of the rim, behind it with their
  hands over the ring, and have dragged the glass their way. The headline
  exists only through the glass: the page's own copy is transparent, and the
  lens is real DOM, a circle of paper carrying a copy set 1.25 times larger,
  offset so it registers with the type beneath, each letter pushed out from
  the lens centre so the lines bow. The reader takes the handle (a real
  button laid along it) and pulls. The six resist: the glass gives 0.62 of the
  hand's travel, lags it on a spring, and creeps back toward them at 40px a
  second whenever the hand rests, a quarter of that while it moves. Within a few pixels of the spot the glass
  clicks in, the waitlist controls pop on two frames, and nothing moves again. Let go
  early and the six win it back on a damped spring. Enter or Space on the
  handle pulls it in for the keyboard, unresisted. A sketched arrow nudging along the
  handle and one caption ("Grab the handle and pull.") are the cue. After the
  first failed pull, the caption says "That all you got?"; after two or more,
  it says "Pull harder!" The assembly is tugged on twos until it settles.
  Coral's eyes stay on the glass;
  the six watch the rim they pull on, then the pointer. The rigs' own
  monocle-sized magnifiers never share a frame with it.
## 5. Motion

- **On twos.** Comic motion is stepped: `steps(2, end)` pops, borders that draw
  on, chips that land on two frames, a cursor that moves in a handful of steps.
  Product UI inside a frame moves like the product (short eased transitions).
- **The demo plays on a clock.** How it works is a product demo, not a scroll
  sequence: it plays like a product video (`story/Demo.tsx`), so a reader can
  watch it or scroll straight past. It runs while at least half of it is on
  screen, stops when it leaves or when the reader presses pause above the rail,
  and loops after the finished Case File has held. Its sixteen beats run about
  74 seconds, played at 0.9 of their authored pace (`PLAYBACK_RATE`); each holds
  as long as it takes to read, so a beat with more to read, like the Product
  Signal or the Case File, holds longer and a simple one moves on sooner. Every frame is a pure function of the clock, so it
  pauses, resumes, and jumps anywhere without replaying what came before.
- **The demo's rail.** The demo's progress lives entirely in its left chapter rail
  (`story/ChapterRail.tsx`). Its six stops stay centered on the rail, with labels 10px
  beyond each marker's edge: black circles with white checks for completed chapters,
  outlined circles for upcoming chapters, and the larger character badge for the active
  chapter. The active character carries a coral ring that fills through that chapter
  with the clock, and a local beat count ("2 of 4"); the final chapter shows "Complete"
  after its last beat finishes. The connecting line records reached chapters. Each stop
  is a button that takes the demo to its chapter's first beat; keyboard focus rings its
  marker and underlines its label, and a click lets go of focus as the demo plays on. Join interview in the
  invitation is a real button too: it takes the demo on into the interview.
- **Phones and reduced motion.** The demo's page is fitted to the width at every
  size, so a phone shows the same demo, smaller. Under reduced motion it does not
  play: it opens on the Request chapter's finished still, and each stop shows that
  chapter's finished still. The hero's city runs while it is on screen; reduced
  motion holds it still and shows the case board and the folder without pops.
- **The hero loads as a whole.** Its copy and its city are revealed together
  once every rig in the city has drawn; the demo beneath it mounts only after
  that. The city's rigs load eagerly and are preloaded from the HTML;
  characters further down fetch their rigs only when they come within a screen
  of the viewport. A dropped rig download is retried with a doubling backoff, so a
  flaky connection does not take the page down; a missing or corrupt rig still fails.
  A new rig draws its first frame before the browser paints, so a character never
  shows an empty canvas first.
- **Upright marks.** Every `<i>` on the page is set upright: they are marks and
  dots, and an italic `<i>` would fetch an italic face nothing uses.

## 5a. The hero's city

Below and beside the copy stands an isometric city (`landing/city/`), drawn from
the reference the team chose: bold ink outlines, flat pastel faces with hatching
on the shaded side, and buildings whose facades are product surfaces (a chat, a
dashboard, a gallery, settings, a form). A V of two coral streets frames the
copy and tells the subhead as one 20-second loop (`city/story.ts`): a Case File
travels the coral street from station to station, and each station acts as it
arrives. Feedback: bubbles float out of the chat tower to coral, side by side so
they never meet. Interview: sky listens to, then speaks with, a customer at the
kerbside table on the café's terrace, where the Case File lies on the table,
shrunk to table size. Pain point: aqua climbs out of a manhole in the street
works with a warning bubble. Build: black waits idle until the case arrives and
the Case File goes into the site, then builds while the crane lowers an app
panel into the one open bay of the two-storey frame going up. What leaves the
site is the build, a small app window that appears at black's side and goes onto
mint's delivery trike; mint carries it up the right arm, parks short of the
shop, and the build hops to the same customer, who gives lilac a thumbs-up and a
heart. Every agent in the city has a step in the loop; the city holds no agent
the story does not reach. The story is shown, never labelled (no signs or
captions in the city), and everything else stays still enough that the carried
token is the thing to follow; add no prop that competes with it.

- **Everything is vector.** Buildings, streets and street furniture are
  projected isometric SVG generated from `city/plan.ts` (ground units, 30 degree
  axes, `city/iso.ts`): paving that follows the grid, recessed roofs behind
  parapets (rooftop units and water tanks only, no plants on roofs), framed
  windows, entrances, lantern lamps, slatted benches. The
  organic pieces (people, trees and shrubs, clouds) were drawn with GPT Image 2
  in the reference's style, traced with vectorizer.ai, ink-thickened to the
  city's line weight and served from `public/landing/city/` (`city/sprites.tsx`
  records each drawing's anchor and scale). The agents are the product rigs
  through `Cameo`, never stills.
- **Layout.** Each side of the V has one tower pair beside the copy, their
  fronts on one line. Below each street, a single row of low shops shares one
  front line near the road and one back line, with a park behind; a small green
  sits under the apex. On the left the café holds the corner, its terrace
  running from the shopfront to the kerb with a strip of pavement before the
  next shop; the street works sit in the middle of the road, in the gap between
  two shops, so nothing covers the hole. The lawns inside the V run the length
  of each sidewalk. The building site is a two-storey frame (glazed ground
  floor, app panels upstairs, one bay left open for the crane) on its gravel
  lot, unfenced, so nothing crosses the building.
- **Depth.** Items are painted back to front; an agent's rig sits between SVG
  planes, and only what stands in front of it and overlaps it rises to a later
  plane (`city/depth.ts`), cropped to its contents so the composited layers stay
  small.
- **The copy stays clear.** `city/fit.ts` records the copy's footprint in design
  pixels (`KEEP_OUT`); `tests/city-plan.test.ts` fails if any roof, rig, crane or
  bubble enters it, or any cloud comes nearer the copy than `SKY_CLEAR`. The
  fit shows 1,900 design pixels across the stage and hangs the city a small gap
  under the waitlist bar, so there is never a band of empty paper between the
  copy and the city; on a phone it grows the city to fill down from the bar.
- **The navbar stays clear.** Nothing in the city touches the navbar: its
  highest visible point (a roof and its rooftop units, a tree, a floating
  bubble; `city/skyline.ts`) keeps `NAV_CLEAR` (32px) of paper under it. The
  side towers are one row deep and at most 9 ground units (three storeys) tall.
  When a stage would still bring the skyline closer, as on wide screens, the fit
  pushes copy and city down together by the least amount that clears it
  (`--hero-push`), and the plan test checks the clearance at every common size.
- **Sky.** Three clouds a side form a loose triangle beside the copy (a small
  puff and a big cloud on top, a medium one below) that never overlaps itself;
  they sit behind the buildings, peeking over and past the side towers. The
  clouds hang lower where needed so the highest keeps the same `NAV_CLEAR` under
  the navbar as the buildings; all six are whole and below the navbar at every
  width. They drift, bob and swell gently, each on its own beat.
- **Motion.** One story clock drives the loop: every position is a pure
  function of its elapsed time, so the city pauses and resumes anywhere. Each
  frame writes the case, the trike, the crane's panel and aqua's climb straight
  to the DOM (`data-mover`); the scene (active station, rig states, which bubbles
  show, the customer's thumbs-up) is React state that changes a few times a
  loop. The carried token's shadow is painted between the ground and every
  item, so whatever stands in front of it covers it; the token floats over the
  city. The stepped loops (flickering windows, bar charts, the people's two
  drawn frames) are switched by the same clock (`city/cycles.ts`), each value
  written only when it changes. CSS runs only the smooth loops, which the
  compositor moves: the chat scrolling, the feedback bubbles, marching chevrons,
  swaying trees and drifting clouds. Travel eases; pops go on twos. The whole
  city pauses when the hero leaves view; reduced motion holds the validated
  moment.
- **Cost.** A CSS animation in SVG gives the animated element its own
  compositor layer, so keep CSS for smooth motion and switch stepped loops from
  the clock (switching the 50 stepped parts this way cut the hero's layers from
  177 to 101 and its idle main-thread time by half). Aqua's rig stops rendering
  while it is down the manhole. The rigs are fetched gzipped (`<rig>.riv.gz`,
  written by the asset sync) and inflated in the browser, as static hosts send
  `.riv` uncompressed.

## 6. The page

| Section | Component | What it is |
|---|---|---|
| Navbar | `landing/Navbar.tsx` | black bar, white mark and wordmark (baked path, `brand/wordmarkPath.ts`); five section links (How it works, Where it helps, For your team, For your users, How it fits), folded into a menu at 1,300px and below so they never crowd the actions; Book a demo and Waitlist |
| Hero | `hero/` | settled: "Ship {features / fixes / workflows} your customers actually want." on one rotating word in Bangers; waitlist email bar, flat on desktop with its Join button hanging under it as a small coral triangle ("Join!", shaded with the headline burst's dot screen) pointing down into the city's V at the 30 degree street angle (`landing/waitlist.css`; on a phone the button sits in the bar). The type scales with the width (the headline at 5.6vw up to 100px, the rotating word at 60px from 1440px wide and stepped down below it, the bar 460px wide); past its caps the copy scales as a block, so it keeps its proportion to the city |
| City | `city/` | the isometric city under the copy (section 5a) |
| How it works | `story/` | the product demo right under the hero, on a clock (section 5): one browser window that opens on Discord and gains the session and preview tabs when Maya joins, plus the workspace and the Case File as windows; six chapters (Request, Interview, Build, Validate, Iterate, Ship) over sixteen beats; a chapter rail on the left, pause above it, one caption over the frame's corner. Over the frame, one line of context names who is who. Request: Maya's ask in #feedback, uTrace's public reply, which others can second with I'm seeing this too (the Product Signal it records stays private), and the private invitation, which states the Outreach Offer ("Dudulemon gets $250 off its next bill.") above a real Join interview button that becomes Discord's grey "Joined" receipt. Ship opens back in Discord, where uTrace thanks Maya and says the credit Dudulemon earned, a hint that For your users explains |
| Where uTrace helps | `helps/` | the case board: one open case, two thumbnails, three situations shown as the concrete product moment (a failing export dialog, a weekend conversion drop with the ask it produced, a spreadsheet comparison built by hand), none of them the demo's plot |
| Case File | `casefile/` | for your team: a coral folder with seven index dividers and three characters looking over its edge; the summary sheet is the same component the demo's last beat renders |
| For your users | `users/` | the Case File's counterpart, crop left and copy right, kept to one picture: Dudulemon's Shopalytics invoice on Stripe's hosted invoice page, 1,000 less Maya's 250 interview credit leaving 750 due, with one caption; beside it the headline, one sentence naming Outreach Offers, and four ticked lines (they sign off the change they asked for; an honest no earns the same credit; rewards scale with the customer's plan and size; you set the budget). No stamp: the page's one stamp is the Case File's |
| How it fits | `fits/` | the setup checklist in the page's order: four numbered bays of ticked rows. Where your customers talk (the physics pile of tiles), your product (the uTrace SDK, preview-only; code access; the safe preview copy), rewards (Stripe or your own credits, the budget, and a link back to For your users), your rules (what agents may change, accounts only humans interview, required checks) |
| Closing | `closing/` | the tug of war: coral on the handle, the six on the right of the rim, the glass arriving pulled right so only the end of the headline shows; the reader pulls it to the spot and the waitlist email bar appears inside the rim |
| Footer | `landing/Footer.tsx` | black; "Forward-deployed agents for product teams."; section links and Contact |

The hero and the navbar's top half are settled. Section links may change;
the hero's words may not without a decision.

The shared waitlist bar has a white email field, slanted coral Join waitlist
button with an arrow, black border and hard ink shadow; on desktop the hero's
bar is flat and its button is the small coral triangle hanging under it (section
6). Focus lightly tints the email field and underlines it in coral through to
the diagonal; keyboard focus also marks the submit button. A valid submission
keeps both bars' size and diagonal: "We’ll be in touch." replaces the input, and
the coral end reads "Joined" with a check drawn on three frames. Focus moves to
the submitting form's confirmation. This shared confirmation resets on reload.
The navbar's Waitlist control focuses the unlocked closing form if its email
field is fully in view; otherwise it returns to the hero and focuses its email
field once the hero is back in place. After joining, those controls focus the
confirmation instead. A keyboard pull also focuses the closing waitlist field or
confirmation.

## 7. Product UI inside the frames

Every crop is the real thing at its own scale, built from `surfaces/`:

- **Discord** (`discord.tsx`): the desktop client at its own proportions and
  palette, filling the tab at Discord's scale: the 72px server rail with the
  selected pill, the 240px channel list (Events, Browse Channels, categories) or
  DM list, the account panel, the chat header with search, day dividers (red with
  NEW where the unread messages start), message groups, the APP tag, embeds
  (blurple for the invitation, green for the credit), component buttons, the
  start of a DM's history, and the composer with who is typing under it. Maya's
  avatar is Dudulemon's logo; other members wear Discord's default avatars. Noto
  Sans stands in for gg sans.
- **Shopalytics** (`ShopalyticsDashboard.tsx`): the conversion dashboard after the
  fixture app, which takes its look from Shopify's admin: a black top bar with
  Shopalytics' logo, search and the Dudulemon workspace (its logo and name), a grey sidebar, the date
  presets and one control per filter dimension, four KPIs, a Catmull-Rom daily
  line in the fixture app's blue, and products beside it. The candidate adds a
  view name field, Save view and the saved views beside the page title. Numbers
  and the drop-off's shape follow the recorded demo runs, not exactly.
- **Stripe** (`users/ForYourUsers.tsx`): the hosted invoice page (merchant name, an
  amount-due card with To and From, the invoice's lines, Pay, "Powered by stripe")
  in Stripe Checkout's own system type, 90% and 60% ink, 10% rules and grey
  discount chip, which carries the interview credit. No coral inside it.
- **The browser frame** (`story/BrowserFrame.tsx`): tabs with favicons (Discord
  alone until Maya joins, then the session and preview tabs beside it), an
  address bar for tabs, a plain title bar for uTrace windows.
- **The session tab** (`story/SessionTab.tsx`): the product's session tab
  (section 7a) at the window's size. The story's implementation is live, so it is
  one Interview Session from the greeting to the follow-up question, and Maya
  never leaves the call. The call fills the left: context and the connection
  pill, the character at 200px, its status in display type, Open preview and the
  four controls. The transcript fills the right with the whole call, following
  the newest turn, including what Maya says while she works in the preview (the
  reset, the partial restore): every turn names its speaker, and the line being
  said types in word by word with the clock. The agent is Wren throughout, drawn
  coral, with a coral dot on its turns; Maya's turns sit on white. In Discord the
  uTrace app posts, and Wren introduces itself by name.
- **The preview tab** keeps its bottom-right corner for the extension orb, set in
  far enough that its four controls fan out inside the tab. The dashboard's
  "View saved" toast is Shopify's, dark at the bottom centre of the window. The
  agent shows what it
  asks about the way the extension draws its scenes (`story/agentScene.ts`, with
  the extension's own geometry and timings): its coral cursor glides to a
  registered target on a bowed curve, rides the coral loop it draws around it,
  overshooting its start, or the line it traces under it, and its label sits under
  the target in a white box with an ink edge and a hard shadow. A mark holds and
  then fades; the cursor fades once nothing moves it. Targets are the dashboard's
  registered elements, measured where they stand (`story/useTargets.ts`). Maya's
  own black pointer glides between the controls she uses and, once she has
  clicked, rests on the chart, clear of what the agent marks. In the partial
  validation she reopens the saved view and the agent circles the review topic
  control, back on All.

Shopalytics' logo (a shopping bag with a rising line) marks its Discord server,
its preview tab, its top bar and its invoice; Dudulemon's marks Maya and the
workspace. Shopalytics, Dudulemon, Maya and every number are fictional and read from one
fixture (`story/shopalyticsFixture.ts`) so the demo and the Case File never
disagree. Illustrative crops say so in a small "Illustrative example" label; the
Case File and For your users each carry one example note. The Outreach Offer's
amounts (the 250 credit, the 1,000 invoice) live in the same fixture, so the demo's
invitation and thanks and For your users agree.

## 7a. The product app

The authenticated surfaces (`app/(product)/`, with `components/product/`,
`session/`, `participant/`, `dashboard/` and `casefile/`) keep the register
without its comic devices; `app/product.css` repeats the section 2 tokens.

- **The bar** is the landing navbar's: 72px of black, 48px sides, and the shared
  `brand/Logo` at 44px with the 36px wordmark.
- **The session tab** takes the whole viewport under the bar. The call fills the
  left: context and a connection pill on top, the character at 300px (200px on a
  short or narrow screen), the status in display type, the implementation
  stepper, Open preview, and the four controls along the bottom. The live
  transcript fills a right-hand column on `--paper-2` behind a 3px ink rule: the
  agent's turns carry its cast colour, the participant's sit on white, the newest
  words carry a stepped caret, and the column follows them unless the reader
  scrolls back. Below 1000px wide the transcript follows the call.
- **Setup, invitation and the end of a session** are one card centred in that
  space, with the setup checklist listing every check before it runs.
- **The participant account** leads with who is signed in, then their
  interviews beside their preview preferences.

## 8. Copy

- PRD vocabulary, exactly: Product Signal, Implementation Gate, Case File,
  Preview Version, Sensitive Account, originating user.
- No release tiers on the page. Marketing may describe the broader product
  vision without matching the current build or demo scope.
- Plain sentences a first-time visitor can parse. If a term needs the PRD to
  understand, either explain it in the next clause or leave it out.
- No lists joined by commas where a sentence would do; no "signature" fragment
  patterns ("bounded by the gate, x").
- Sentence case everywhere, including buttons, dividers and stamps.

## 9. Working on it

- Tests: `cd apps/web && npx tsc --noEmit -p tsconfig.json && npx eslint . && npx vitest run`.
  The city plan test keeps the hero's city out of the copy and its depth order acyclic.
  Story tests cover the beat table, the Discord opening and Join interview, the
  live transcript, the thanks and the finished Case File, the reduced-motion
  stills, and the agent's scene; the case board and the folder have
  interaction tests; the smoke test
  pins the section order, the navbar, email fields and contact links.
- Visual proof is headless: capture frames with playwright-core (the dev server
  from `.claude/launch.json`, `web`), sample rigs on a cadence, diff consecutive
  frames, measure overlaps with a separating-axis test. Never ask the reader to
  check by hand.
- Rig work needs the Rive editor open for the MCP; keep one rig per file, and
  rebuild from `packages/utrace-agent-character/rive/`.
- Documentation diagrams follow `.agents/skills/general/utrace-pele`.
