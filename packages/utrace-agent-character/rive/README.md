# Rive character exports

Seven rigged and animated agent characters, one `.riv` per body. The final files
live in `packages/utrace-agent-character/assets/` (the app package and the
landing page load them from there); this folder holds the build tooling and the
viewer. They are built from the
character vectors (`./source/vectors/`) and animated to the
motion language of xAI's Grok Bot, which the user sampled at 120 fps as the
reference: eyes squeeze shut a few frames before any body change, the change
itself takes 100 to 300 ms with a single overshoot, secondary elements draw on
and off along their paths, every hold keeps bobbing, and eyes return by
growing from small dashes. Each file is a Rive cloud file of the same name in
Rithvik's workspace (`utrace-agent-<colour>`; the first-generation files are
kept as `utrace-agent-<colour>-v1`).

| File | Body | Eye line |
| --- | --- | --- |
| `utrace-agent-coral.riv` | circle | 105 |
| `utrace-agent-black.riv` | rounded square, white eyes, white-rimmed ink accessories | 105 |
| `utrace-agent-mint.riv` | hexagon | 105 |
| `utrace-agent-sky.riv` | bean | 100 |
| `utrace-agent-lilac.riv` | triangle | 118 |
| `utrace-agent-peach.riv` | pill | 92 |
| `utrace-agent-aqua.riv` | arch | 104 |

## Runtime contract

Artboard `AgentCharacter` (300 x 300, transparent), state machine
`AgentCharacterMachine`, auto-bound view model `AgentCharacterModel` with the
enum `state` carrying all fourteen spec states in order: `idle, investigating,
listening, thinking, speaking, acknowledgement, concern, building, completion,
ship, reconnecting, paused, sleep, pointer`, plus the numbers `gazeX`, `gazeY`
(eyes follow within 12 px; `pointer` is idle with the gaze bound), `speechEnergy`
(scales the middle bubble dot) and `motionScale` (declared, not yet bound).
`packages/utrace-agent-character` enforces this fourteen-value enum in
`src/contracts.ts`; the earlier ten-value placeholder asset has been removed and
the web surfaces load the coral character until the landing redesign picks
per-surface characters.

## Motion design

Every state with an accessory or a morph has an `_in` animation, a loop, and
where it matters an `_out`; the state machine chains them with exit-time
transitions and returns through `idle`:

- `idle` (6 s loop): breathing, two blinks, a glance, a squint.
- `investigating`, `listening`, `building`: eyes shut for four frames, the
  accessory drops in with an elastic bounce, the body squashes as it lands,
  the eyes reopen while it settles; the exits lift the accessory off.
- `building` orbits: six comet streaks (trim-path segments on tilted ellipses),
  one in the body colour of each of the other six characters, sweep in, circle
  the head at different speeds with four of them passing behind it, and draw off
  on exit. Where a partner colour sits within 45 degrees of hue and 0.15 of
  lightness of the body it is darkened by 0.16 in OKLCH lightness so it still
  reads (`gen/streaks.json`, six such pairs). This replaces the closed loops of
  the first pass, which sat flat on the body.
- `thinking`: eyes shut, the body collapses to the universal 18 % dot while
  the middle dot grows out of it and the outer dots slide apart; a brightness
  wave runs left to right; the exit merges the dots and regrows with overshoot.
- `speaking`: the bubble pops in elastically, dots type, the body bounces per
  phrase, the exit shrinks the bubble away.
- `acknowledgement`: one squash and stretch with arc eyes, one eye raised.
- `concern`: tilt, narrow, eyes lean in; the loop wobbles.
- `completion`: the stamp rises, presses with a squash, the check pops with an
  elastic scale, arc eyes; holds until the app changes state.
- `ship`: satchel flies in, look right, wind up, pill, dot, trail draws on,
  dash off right; the exit brings the dot back and regrows.
- `reconnecting` (4 s loop): squeeze into an exclamation with a popped dot,
  wobble, relax with overshoot, blink, repeat.
- `paused`, `sleep`: slits and sink; the sleep dot scatters two particles that
  drift while it bobs, then regrows on exit.

Transition mixes are 100 ms in and 160 ms out; keyed timings do the rest.

## Rig

`Root` (design position 150, 165) holds the body, an `Eyes` group (ovals and
arc variants), the traced deerstalker, magnifier, hard hat, stamp and satchel
imported as SVG assets, a vector headset (rounded cups pinned to the body edge,
a cubic band fitted to the body's contour from `gen/band_cubics.json`, a boom
that ends under the mic, which is a group with an opaque body-colour fill so the
boom's end stays hidden while the mic pulses), the bubble, the check, and the
orbit streaks in `OrbitsBack` (behind the body) and `OrbitsFront`. `Dots`, `Trail`,
`ExclaimDot` and `Particles` sit on the artboard so nothing shrinks with the body.

The body is a 120-vertex polygon with corner rounding, sampled by arc length
from the exact silhouette (`gen/morph.py`, sharp corners such as the arch's base
pinned exactly with no rounding), so it can morph: the thinking, sleep and
ship collapses go silhouette to a pinched silhouette to a small copy of the same
silhouette (the circle keeps a true teardrop) and back, so every character stays
recognisable while collapsed; the three thinking dots are small copies of the
body too; reconnecting goes pinch to a tapered exclamation bar, which stays a
bar because it is a glyph. Every morph target is sampled at each
vertex's own polar angle, so vertex i always maps to vertex i, and only x and y
are keyed. Handle properties of cubic vertices are not animatable at runtime,
which is why the body is a rounded polygon rather than a bezier path.

Every animation keys the whole neutral pose at frame 0, because a state machine
keeps whatever the previous animation left behind. Stroke-only paths (band, boom,
arcs, tick, trail) are explicitly left open; Rive closes created paths by
default, which draws a chord across them. The imported hats rest at y -100 like
every other import, so their drop-in keys are written relative to that.

## Rebuilding and verifying

`gen/build_character.py <colour>` rebuilds one character in place in the ACTIVE
Rive file (it deletes the previous `AgentCharacter` artboard first, so open the
right file) and exports it; `gen/run_character.sh <colour>` also captures the
morph states at 30 fps with the real web runtime and writes review sheets. The capture scripts
need `playwright-core` and the `@rive-app/canvas` runtime (`rive.js` and
`rive.wasm` in `gen/rt/` or `RIVE_RT`); `rive-zoom.mjs` renders one state at
1000 px with the app's webgl2 runtime for edge checks, and `rive-anim.mjs`
logs when dark pixels change to time an animation. All of them talk to the
Rive MCP server over `http://127.0.0.1:9791/mcp` (`gen/mcp.py`): never send
`initialize` there (one global session; the editor's tool session breaks until
`notifications/initialized` is resent), the editor is sandboxed (assets go in
as data URIs, exports come back inline as base64), and the Rive desktop app
ignores background clicks, so creating and renaming files needs screen control.

## Findings from the landing rig builds

Two findings worth knowing before the next rig:

- The editor's `export_file` carries only the first artboard of a file, so
  every exported rig needs its own Rive file (as the seven characters have).
  A second artboard in the same file also collides on the view-model name
  (`listViewModels` reports nothing, so the builder cannot detect the first).
- When Claude Code's Rive MCP client failed to connect (Rive was not running
  at session start), the HTTP endpoint has no session: send `initialize` and
  `notifications/initialized` once, then the tool calls work. `gen/mcp2.py`
  is the SSE-tolerant client the landing builders use; the character builder
  still uses `gen/mcp.py`.

## Landing rigs (pass 2)

Run with Python 3.11+ against an already initialized MCP HTTP session. Both
builders use `gen/mcp2.py` through `gen/landing_common.py`, require explicit
success from every MCP call, and replace their previous artboard on every run.
Open a dedicated file for each rig/colour: the active file must be empty or
contain only that builder's named artboard, since `export_file` exports only
the first artboard. They leave other files alone. The local
`gen/.landing-<colour>-bindings.json` and
`gen/.stagehand-<colour>-bindings.json` files retain view-model/converter IDs
by artboard ID so reruns can reuse them even when `listViewModels` omits them;
keep these local files between runs. This is binding bookkeeping, not an
animation resume mode.

`python3 packages/utrace-agent-character/rive/gen/build_landing.py <colour>` supports
`coral`, `black`, and `mint`. It builds transparent artboard `LandingAgent`
(300 × 300), state machine `LandingMachine`, and auto-bound `LandingModel`.
Every colour exposes the `pose` property using data enum `LandingPose`, in
this order:

`idle, investigate, realise, listen, speak, nod, concern, build, repair, done, present`

Unsupported poses are named states using the idle animation. Coral authors
investigation and conversation; black authors build, repair, and done; mint
authors present and nod. The deerstalker/magnifier persist across
`investigate`/`realise`, the headset across `listen`/`speak`/`nod`/`concern`, and
the hard hat across `build`/`repair`/`done`: their opacity is keyed to 100 at
frame 0 of every family member. Family changes mix directly over 160 ms;
leaving a family uses its exit animation before returning through idle.
Compound `notEqual` conditions prevent family exits during internal changes.
Black's `repair_out`/`done_out` reuse the build exit's hat lift while keeping
orbits off; `done_out` also shrinks the check away.

One-shot interpretation: `realise` and `nod` play once while their enum value
is held. Exit-time transitions lead to `realise_rest`/`nod_rest` states using
the family's resting animation (`investigate`/`listen`, or mint's `idle`).
These aliases prevent immediate retriggering; changing `pose` rearms the
gesture. Black's `done` and mint's `present` are non-looping and hold their
last frame until `pose` changes. Mint's hands retract over 12 frames on exit;
the page draws the document between them. No speech bubble is included.

`gazeX`/`gazeY` take values in −1..1 and use the reference's 12 px converters
on `Eyes`, including the eye-line Y offset. `energy` takes 0..1 and is bound
to `Root.scaleY` with the requested formula `100 + {Input} * 3`. Root scaleY
also has animation keys: coexistence and scale units need confirmation in
the live Rive 0.6 run. A rejected binding stops the build; it is not silently
omitted. These builders have only been checked locally, without the editor.

`python3 packages/utrace-agent-character/rive/gen/build_stagehand.py <colour>` supports
`mint` and `black`. It builds transparent artboard `StagehandMint` or
`StagehandBlack` (300 × 300), Root at (150, 176), and `StagehandModel` with
`gazeX`/`gazeY`. Gaze uses 10 px X / 8 px Y converters with the
eye-line offset. The two rimmed hands grip the page-drawn vertical cord at
x +58. Its only linear animation, `pull`, lasts 72 frames at 60 fps and does
not loop: hold/glance (0–8), pull down 56 px (8–48), settle (48–60), then
release and move 8 px right (60–72). Every animated property has keys at 0
and 72. All easing is cubic; the settle's single overshoot is explicitly
keyed so backward scrubbing is deterministic. `StagehandMachine` keeps its
default contents with no added states or transitions; do not play it. The
page drives `rive.scrub("pull", t)` directly.

Exports are written to `apps/web/public/landing/utrace-landing-<colour>.riv`
and `apps/web/public/landing/utrace-stagehand-<colour>.riv`, respectively.
