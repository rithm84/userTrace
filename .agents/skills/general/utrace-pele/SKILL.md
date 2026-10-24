---
name: utrace-pele
description: uTrace conventions for the documentation diagrams under docs/diagrams — Mermaid sources rendered by Pele, choosing the primitive that models each subject (flowchart, sequence, state, swimlane, ER, mindmap), semantic node shapes, role classes and edge kinds the shared theme colors in light and dark, invisible row subgraphs for grids, the generated key, label limits, the render-and-inspect loop, and the Pele hazards already hit. Read in full before creating or editing ANY uTrace documentation diagram.
---

# uTrace Pele diagram conventions

Diagrams are text. Each `docs/diagrams/pele/<name>.mmd` is Mermaid source that
the pinned [Pele](https://pele.run) renderer turns into the paired
`docs/diagrams/svg/<name>.svg` the docs embed. Nothing is drawn by hand.

## Setup

- `pele` is pinned in the root `package.json`; run `npm ci` once.
- `npm run diagrams` renders every source, and `npm run diagrams -- NAME...`
  renders only those. Output is deterministic, so a second render must produce
  no diff.
- `docs/diagrams/render.mjs` embeds `docs/diagrams/pele/_theme.css` in every
  export and sets a 360 px label wrapping width. Diagrams carry no key of their
  own: every run writes the one key of all roles to
  `docs/diagrams/svg/_key.svg`, which the docs show under every embed (see
  [Embedding](#embedding)). Files whose name starts with `_`, in `pele/` or
  `svg/`, are shared files, not diagrams.
- Never hand-edit an SVG. Edit the `.mmd`, render, and commit both files.

## Hard rules

1. Every edge label is the connection's own label (`a -->|label| b`), never
   floating text.
2. A diagram draws relationships and flows. Enumerable lists (state names,
   field lists, closed sets, rule bullets) live in the markdown as
   code-formatted terms, never in a text-heavy node.
3. Domain language matches the PRD exactly; lifecycle states keep their
   snake_case spelling.
4. Sources carry no colors: no `style`, no `linkStyle`, and no `classDef` with
   colors. Colors set that way are fixed and ignore dark mode. Use roles and
   edge kinds instead.
5. Keep the export at or under 1,400 px wide; `render.mjs` warns past that,
   because the docs scale a wider diagram down until its text is too small.
6. When a remodel drops a fact from a diagram, the fact must already be in the
   surrounding prose or move there. `docs/PRD.md` is user-owned: ask before
   editing it.
7. After any change, render, look at the preview, then run the doc-verify
   commands in AGENTS.md.

## Choose the primitive that models the subject

Pick by what the diagram is about, not by how the previous version looked.
Different diagrams use different primitives.

| The diagram shows | Primitive |
| --- | --- |
| Components, stores, and how they connect | `flowchart` with boundary subgraphs and semantic shapes |
| Messages between actors in time order: setup, handshakes, request paths | `sequenceDiagram` with `autonumber`, `Note over`, and `alt`, `opt`, `loop`, or `par` blocks |
| A lifecycle or state machine | `stateDiagram-v2` with `[*]`, labeled transitions, and nested states |
| One journey handed between owners | `swimlane-beta`, one lane per owner |
| Records and their cardinality | `erDiagram` |
| A breakdown of one concept into parts | `mindmap` |
| Peer components in a cycle | `flowchart` laid out with `row_*` subgraphs (below) |

Avoid `block-beta` (it rejects markdown labels and `:::` roles),
`architecture-beta` (its icons need a resolver `render.mjs` does not pass), and
C4 (it ignores the roles).

## Shapes

| Node | Shape |
| --- | --- |
| Service, controller, worker | `id[Label]` |
| Durable store | `id[(Postgres)]` |
| Queue or stream | `id@{ shape: h-cyl, label: "Queue" }` |
| Document or artifact (Case File, PR) | `id@{ shape: doc, label: "Case File" }`; many: `docs` |
| Decision | `id{Product Signal?}` |
| Gate or check | `id{{Implementation Gate}}` |
| Person, surface, start or end | `id([Originating user])` |
| Input | `id[/Discord message/]` |

## Roles

Name a role by what the node is: `api[FastAPI]:::runtime`. In a state diagram,
write `class closed_validated outcome` or `state:::outcome`. Sequence diagrams
carry no roles. The theme sets fill and stroke; text stays the ink color.

| Role | Use for |
| --- | --- |
| `people` | People, customer surfaces, browsers and tabs |
| `runtime` | uTrace control plane, workers, ingestion |
| `reasoning` | Voice agent, GPT-6.1 Sol, Jev |
| `observation` | Browser SDK, Server SDK, extension, previews |
| `external` | Providers, terminal blockers, refusals |
| `outcome` | Validated outcomes, durable records |
| `async` | Async paths, iteration, pauses |
| `gate` | Gates, checks, and decisions |
| `neutral` | Closed or neutral states, zones |
| `client` | Client product code |

## Edges

- `-->` is the default, `-.->` an optional or conditional relationship, `==>`
  the primary path (sparingly), and `<-->` both directions.
- An edge kind is the prefix of the edge's id: `async_1@-.->` (orange, async
  paths, iteration, pauses), `blocked_1@-->` (red, refusals and blocked
  outcomes), `ok_1@-->` (green, validated success). Ids must be unique. Each
  kind shares its color with a role (`async`, `external`, `outcome`), so the
  key shows no arrows.
  Edge kinds work in flowcharts and swimlanes; state transitions take no ids.

## Labels

- Prefer short node labels and labeled edges. When a node needs detail, use a
  markdown string: a bold heading and at most three lines of up to about 45
  characters. Lines are centered, so skip bullet glyphs:

  ```mermaid
  api["`**FastAPI · Supabase Compute**
  Authorize the current user
  Keep provider keys server-side`"]:::runtime
  ```

- Quote any label that contains `(`, `)`, `{`, `}`, `|`, or `#`.
- Give each diagram a title in frontmatter (`title: ...` between `---` lines).
  It becomes the SVG's accessible name and is not drawn, because the prose
  around the embed already introduces the diagram. Never add a title inside
  the diagram body.

## Layout

- Use `LR` for a chain of up to about four hops and `TB` for anything longer.
- Any subgraph draws a dashed boundary with its label: use one per tier,
  platform, or trust boundary.
- A subgraph whose id starts with `row_` draws nothing. Use it to lay out a grid:
  an outer `flowchart TB` holds `row_1`, `row_2`, … subgraphs with
  `direction LR`, and a return row uses `direction RL` so a cycle reads
  clockwise. Edges between rows make the turns. Within a row, edges run one
  way, in chain order; chain nodes that have no edge between them with
  invisible links (`a ~~~ b ~~~ c`) or the row stacks them vertically.
- A boundary subgraph can sit inside a grid row, and an edge can target a
  subgraph id (`revoke -->|narrows| staff_access`).
- In a state diagram, declare `state "Title" as id` first, then add detail
  lines with `id : line`; state labels take no markdown strings, and detail
  lines wrap at about 220 px.
- Never draw a key inside a diagram; the one key in `svg/_key.svg` covers
  every diagram.

## Embedding

Embed a diagram as its own paragraph. When it uses roles or edge kinds, follow
it with the collapsed key, so a reader opens the key in place; GitHub renders
the block as one "Diagram key" line. A diagram without colors, such as a
sequence diagram, takes no key block. Use the same relative directory as the
diagram's path:

```markdown
![Alt text that states what the diagram shows.](../diagrams/svg/<name>.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>
```

The empty paragraph gives the closed block the same space below it as a
paragraph in viewers that leave none; on GitHub its margin merges with the one
GitHub already sets, so nothing changes there.

The AGENTS.md doc check fails when a colored diagram lacks this block or an
uncolored one has it.

## Render and inspect loop

1. `npm run diagrams -- <name>`.
2. Preview at full size with headless Chrome, using the width and height the
   render printed:

   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
     --hide-scrollbars --window-size=<width>,<height> \
     --blink-settings=preferredColorScheme=1 \
     --screenshot=/tmp/<name>.png "file://$PWD/docs/diagrams/svg/<name>.svg"
   ```

   `preferredColorScheme=1` is light and `0` is dark; check both when colors
   change. Quick Look (`qlmanage`) crops a tall diagram to a square, and
   `rsvg-convert` cannot resolve CSS variables, so neither gives a usable
   preview.
3. Check the width the render prints, overlaps, cut labels, and that every
   edge label sits on its edge. Fix the source; prefer a better primitive or
   shorter labels over layout tricks.
4. Run the AGENTS.md doc-verify commands.

## Hazards

| Hazard | Rule |
| --- | --- |
| `**bold**` outside a markdown string | Renders literally; wrap the label in backticks inside the quotes |
| `block-beta` labels | No markdown strings and no `:::`; use a flowchart |
| `classDef`, `style`, or `linkStyle` colors | Inline and fixed; they ignore dark mode |
| A long `LR` chain | Exceeds 1,400 px; switch to `TB` or a `row_*` grid |
| An edge kind on an edge without an id | Uncolored; write `async_1@-.->` |
| Two edges with the same id | Invalid; number the ids |
| A role name outside the table | `render.mjs` fails with "unknown role class" |
| Gantt charts without `now` | Today markers depend on the render date; avoid them in docs |
| `class a, b role` | Valid; `render.mjs` still checks the role name |
| `direction LR` on a subgraph of unconnected nodes | Stacks them; chain them with `~~~` or leave `direction` off |
| A state transition naming a state inside another composite | Creates a duplicate empty state; keep transitions within one composite or name the composite |
| `;` in a note or a state description | Starts a new statement and breaks the parse |
| `<` or `>` in a sequence label | Write `#lt;` and `#gt;` |
| `create participant` | Places the lifeline at the far right; declare participants up front |
| Sequence `box` | Renders as a shaded region; message labels across its edge stay readable |
