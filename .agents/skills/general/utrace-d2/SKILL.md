---
name: utrace-d2
description: uTrace conventions for the D2 documentation diagrams under docs/diagrams — the shared style include, role palette, the markdown-card row-grid layout every component diagram uses, when TALA replaces the grid, where the key goes without growing the canvas, readability rules that keep text large and boxes tight, the render-and-inspect loop, and the D2 hazards already hit. Read in full before creating or editing ANY uTrace documentation diagram.
---

# uTrace D2 diagram conventions

Diagrams are text. Each `docs/diagrams/d2/<name>.d2` compiles to the paired
`docs/diagrams/svg/<name>.svg` that the docs embed. Nothing is drawn by hand.

## Setup and layout

- D2 `v0.9.0` (`brew install d2`). TALA is bundled; no license key.
- `docs/diagrams/d2/_style.d2` is the shared include: `d2-config`
  (ELK layout, sketch mode, theme 0, pad 32) and every class below. Sources
  whose basename starts with `_` are includes, never diagrams.
- `docs/diagrams/render.sh [name...]` renders sources to SVG and always passes
  `--sketch`. Output is deterministic, so a second render must produce no diff.
- Never hand-edit an SVG. Edit the `.d2`, render, and commit both files.

## Hard rules

1. Every edge label is the connection's own label (`a -> b: label`). Floating
   text beside a line is a defect.
2. A diagram draws relationships and flows only. Every enumerable list (state
   names, field lists, closed sets, rule bullets) belongs in the markdown as
   code-formatted terms; a text-heavy "rules" card inside a diagram is a defect.
3. Domain language matches the PRD exactly; lifecycle states keep their
   snake_case spelling.
4. Text stays large: title 30, card headings from `###`, body 19, node labels
   19, edge labels 18, never smaller than 17. If a render exceeds about
   4,400 px wide or 4,000 px tall at D2's 2x PNG scale, fix the layout, not
   the font.
5. Boxes hug their content. A card is one markdown rectangle, never a
   container with padding around a child, and it holds at most five bullets
   of about 45 characters. No icons: an icon on a markdown shape inflates it
   into a square.
6. After any change, render to PNG, look at it, then run the doc-verify
   commands in AGENTS.md.

## Roles

Pick a role by what the node is, and combine it with a structural class:
`class: [card; runtime]`. Roles set fill and stroke only; text is always the
dark ink color.

| Role class | Use for |
| --- | --- |
| `people` | People, customer surfaces, browsers and tabs |
| `runtime` | uTrace control plane, workers, ingestion |
| `reasoning` | GPT-Live, Opus 5.5, Jev |
| `observation` | Browser SDK, Server SDK, extension, previews |
| `external` | Providers, terminal blockers, refusals |
| `outcome` | Validated outcomes, durable records |
| `async` | Async paths, iteration, pauses |
| `gate` | Gates, checks, and decisions drawn as cards |
| `neutral` | Rule cards, closed or neutral states, zones |
| `client` | Client product code |

## The standard layout: one grid of cards

Every component diagram is one grid of markdown cards with labeled edges, and
the title is the grid's own label (`class: grid` renders it at top-center with
no extra band). Give the grid both `grid-rows` and `grid-columns` and declare
cards in row-major order; an invisible spacer cell holds any empty position so
every card keeps its column, and one spacer becomes the key:

```d2
...@_style

cells: "Diagram title" {
  class: grid
  grid-rows: 2
  grid-columns: 3
  horizontal-gap: 180
  vertical-gap: 80

  tab: |md
    ### Session tab

    - One statement per bullet
    - A wrapped continuation stays in its bullet
  | {class: [card; people]}
  api: |md
    ### FastAPI · Compute

    - Authorize current user
  | {class: [card; runtime]}
  live: |md
    ### GPT-Live

    - Full-duplex voice
  | {class: [card; reasoning]}

  _s1: "" {width: 8; height: 8; style: {opacity: 0}}
  store: |md
    ### Postgres

    - Durable workflow truth
  | {class: [card; outcome]}
  key: "" {
    class: legend
    grid-rows: 2
    grid-columns: 2
    horizontal-gap: 8
    vertical-gap: 6
    k_people: "People · customer surfaces" {class: [key; people]}
    k_runtime: "uTrace control plane" {class: [key; runtime]}
    k_reasoning: "Reasoning models" {class: [key; reasoning]}
    k_outcome: "Validated · durable" {class: [key; outcome]}
  }

  tab -> api: "SDP offer" {class: edge}
  api -> store: "commit" {class: edge}
}
```

- Edges inside a grid are straight lines, so an edge may only join
  neighbours (including diagonal neighbours), and two edges between the same
  pair overlap exactly. There is no clean way to route a second edge: an
  invisible waypoint cell stretches to its column so the segments stop short,
  and a visible junction dot looks wrong in sketch mode. Merge the two into one
  edge with one label, or state the second relationship as a bullet on the
  destination card.
- A label on a horizontal edge between neighbouring cells must fit in the gap.
  Wrap any label longer than about 14 characters with `\n` near its middle;
  diagonal and vertical edges have more room and stay on one line.
- Use `horizontal-gap: 180` to `200` and `vertical-gap: 80` to `90` when the
  grid has edges; `60` and `36` when it has none.

## When the grid cannot hold the edges

Switch to TALA only when an edge would cross a cell (two cells apart in one
row or column) or two edges join the same pair of cards. Then drop the grid,
declare the cards at the top level, and add after the import:

```d2
vars: {d2-config: {layout-engine: tala}}
direction: down
```

TALA is sensitive to declaration order: try the flow order and its reverse,
try `direction` values, and keep the render closest to the original aspect
ratio. Do not pin cards with `near` under TALA; it inflates the canvas.

## Key

Every diagram carries a key: one chip `k_<role>: "<label>" {class: [key; <role>]}`
per role the diagram uses, with the standard labels People · customer surfaces,
uTrace control plane, Reasoning models, SDKs · previews, External · blocked,
Validated · durable, Async path, Gates · checks, Neutral · closed, Client
product. A non-default edge style adds a `key-edge` chip whose text is the edge
color's meaning ("orange arrow · async") in that edge's font color. The chips
sit in one `key` grid with `class: legend`, and the grid goes where whitespace
already exists, in this order:

1. **An empty grid cell.** Replace the spacer with a nested two-column `key`
   grid (`horizontal-gap: 8`, `vertical-gap: 6`), preferring the top-right,
   then bottom-left, bottom-right, and top-left cell. The nested grid
   stretches to the cell; its 60-unit container padding can widen that column
   a little, which still beats a new band.
2. **Open space in a TALA layout.** Declare the key unpinned at the top level
   and let TALA place the disconnected component. Pick the column count that
   fits the hole (one column for a tall gap, three for a wide one) and compare
   the canvas with a render that has no key; if the canvas grew, use rule 3.
3. **A full grid.** Pin the key with `near: bottom-center` as a single row (up
   to eight chips; every grid is wider than that). This costs about 80 units
   of height, most of which the grid-label title gives back.

Never wrap the diagram and the key in an outer grid, never pin chips one by
one (shapes on the same `near` constant stack on one spot), and never use a
corner constant such as `top-right`: D2 places corners diagonally outside the
diagram and widens the canvas.

## Other patterns

- **Node** (`node`): a plain labeled shape for name-only boxes.
- **Zone** (`zone`): dashed container for boundaries and tiers.
- **State** (`state` + a role) with `decision` diamonds for gates.
- **Store** (`store`): cylinders for durable stores.
- **Sequence**: `shape: sequence_diagram` with actors as children and
  messages as edges; role classes color the actors.
- **Edges**: `edge` (default), `edge-dashed`, `edge-async`, `edge-blocked`,
  `edge-ok`. `<->` draws both heads.
- **Title**: a grid diagram's title is the grid label (`class: grid`). A TALA
  diagram has exactly one `{class: title}` text; it renders at `top-center`,
  and `top-left` opens an empty band beside the diagram.

## Render and inspect loop

1. `d2 --sketch docs/diagrams/d2/<name>.d2 /tmp/<name>.png` and look at the
   PNG. Check overlaps, cut labels, edge labels sitting on lines, holes in a
   row, and the size limits in rule 4.
2. Fix the source. Prefer moving a card to the right row and column, shorter
   labels, or splitting an overloaded card over layout tricks.
3. `docs/diagrams/render.sh <name>`, then the AGENTS.md doc-verify commands.

## Hazards

| Hazard | Rule |
| --- | --- |
| `{` in a label (`/sessions/{id}`) is parsed as a map | Quote the label |
| Markdown label without `shape: rectangle` | Becomes a borderless text shape; the `card` class sets the shape |
| An icon on a markdown card | Box inflates to a square; no icons |
| A container card (label + body child) | Fixed engine padding leaves a third of the box empty; use one markdown shape |
| `grid-rows` without `grid-columns` | Cells spread evenly across rows; set both and use spacer cells |
| Nested grids, one per row | Rows do not align; use one grid with spacers |
| Edges between non-neighbouring grid cells | Straight lines cross cards; move the card or use TALA |
| Two edges on one pair of cells | They overlap exactly; merge them or move one relationship into a card bullet |
| A waypoint cell or junction dot for a loop-back | Stretched cell or ugly dot; grids cannot route, see above |
| A label on a horizontal grid edge longer than about 14 characters | Runs into both cards; wrap it with `\n` |
| `direction` inside a container | Ignored by ELK; plan one top-level flow |
| ELK on a map of cross-linked cards | One very wide row; use the row grid |
| `near` on a card under TALA | Canvas inflates; leave cards unpinned |
| A bullet with embedded newlines | Indented lines become a code block; join lines or use two-space hard breaks |
| A card heading wider than every body line | Wraps mid-word whatever `width` says: a markdown card is as wide as its longest body line, so give it one body line at least as long as the heading |
| A wrapper container around the whole diagram | Every container adds 60 units of padding on each side; use the grid label for the title and put the key in a cell |
| A corner `near` constant (`top-right`, `bottom-left`) | Placed diagonally outside the diagram; only `top-center` and `bottom-center` sit in a band |
| Several shapes pinned to one `near` constant | They stack on the same spot; group chips in one `legend` grid |
