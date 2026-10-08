# AGENTS

Operational guide for coding agents working in uTrace.

## Project Overview

uTrace is a forward-deployed agent system for product teams. It routes
customer signal into the right interview flow, observes real product usage
through structured browser and server telemetry, and asks short,
context-aware questions inside the user's own workflow. From that evidence it
synthesizes Product Signals, sends eligible work through the Implementation
Gate, implements only inside isolated workspaces, and returns previews to the
originating user for validation. Validated work ships as a Case File
with evidence, risk, and implementation status, and a PR opens only for
validated implementation work.

## Repository Layout

```
uTrace/
├── docs/
│   ├── PRD.md                     # Demo product requirements — scope + vocabulary source of truth
│   ├── ARCHITECTURE.md            # Demo architecture + active-provider lookup
│   ├── SCENARIO.md                # The Shopalytics demo journey
│   ├── DESIGN.md                  # Design system: register, tokens, cast, devices, motion, page map
│   ├── arch-components/           # One doc per architecture component (diagram + prose)
│   └── diagrams/
│       ├── svg/                   # Exported SVGs embedded by the docs
│       ├── d2/                    # D2 sources; same basename as the paired SVG
│       │                          #   (_style.d2 is the shared include)
│       └── render.sh              # Renders every source with --sketch
├── apps/
│   ├── web/                       # Landing page and product web surfaces (nested AGENTS.md)
│   ├── api/                       # FastAPI control plane and workers
│   ├── preview-front-door/        # Vercel preview front door
│   └── browser-extension/         # Manifest V3 extension
├── packages/
│   ├── utrace-agent-character/    # Shared landing runtime, seven .riv characters,
│   │                              # editable artwork, and Rive authoring tools
│   ├── utrace-browser-sdk/        # Embedded browser SDK
│   ├── utrace-server-sdk/         # Node.js server SDK
│   └── utrace-contracts/          # Versioned schemas and Python/TypeScript bindings
├── tools/demo-harness/            # demo prepare, reset-target, and watch
├── tests/e2e-browser/             # Browser end-to-end tests
├── supabase/schemas/              # Declarative schema; schema migrations are generated from it
├── supabase/migrations/           # Versioned SQL migrations
├── deploy/                        # Control-plane and sandbox deployment
├── assets/                        # Logos and brand assets — user-owned, ask before editing
├── .agents/skills/                # Installed skills: utrace-providers/, general/,
│                                  #   browser-related/
├── skills-lock.json               # Canonical skill inventory
└── AGENTS.md                      # This file (CLAUDE.md symlinks here)
```

`docs/` specifies the demo: the Shopalytics journey and the product and
architecture it needs. The docs describe the system to build: where the code
differs, the docs are the target.

The landing page is a marketing surface for the broader product vision. Its
claims and illustrations need not match the current build or demo scope; do not
narrow them solely to align with implementation or release boundaries.

## Required Read Order

At the beginning of a session, and again after any context compaction, you
MUST read these in order before doing anything else:

1. `docs/PRD.md`
2. `docs/ARCHITECTURE.md`
3. `docs/SCENARIO.md`

`docs/DESIGN.md` is not part of the default set: read it before any design
work (UI, visual language, landing surfaces, diagram aesthetics).

If supporting docs or code disagree with the PRD or architecture, call out the
disagreement to the user even when it is adjacent to the task.

## Domain Language

Use the PRD's vocabulary exactly, in every doc, diagram, commit, and comment.
Canonical terms, capitalized as shown: Product Signal, Intake Item,
Implementation Gate, Implementation Attempt, Preview Version, Case File,
Sensitive Account, Interview Session, Customer Surface, Outreach Offers, originating user.

- Good: "the Implementation Gate approves an async Implementation Attempt"
- Bad: "the ticket is approved and becomes a dev task"
- Good: "re-contact the originating user to validate the Preview Version"
- Bad: "ping the customer to check the build"

Lifecycle states are code-formatted snake_case (`gate_pending`,
`closed_blocked`, `awaiting_validation`). Demo behavior belongs in `docs/`. Do not
introduce release tiers as substitutes for explicit scope.

## Commands

Verify docs after any documentation or diagram change (all embeds resolve,
SVG/source pairs match, exports are fresh, no Mermaid — D2 is the standard):

```bash
python3 - <<'EOF'
import re, glob, os, subprocess
bad, total = [], 0
for f in glob.glob("docs/**/*.md", recursive=True):
    base = os.path.dirname(f)
    for m in re.finditer(r"\]\(([^)]+\.svg)\)", open(f).read()):
        total += 1
        if not os.path.exists(os.path.normpath(os.path.join(base, m.group(1)))):
            bad.append((f, m.group(1)))
svg = {os.path.basename(p).removesuffix(".svg") for p in glob.glob("docs/diagrams/svg/*.svg")}
exc = {os.path.basename(p).removesuffix(".d2") for p in glob.glob("docs/diagrams/d2/*.d2") if not os.path.basename(p).startswith("_")}
merm = subprocess.run(["grep", "-rn", "```mermaid", "docs/"], capture_output=True, text=True).stdout
print(f"{total} embeds, {len(bad)} missing; pairs match: {svg == exc}; mermaid fences: {len(merm.splitlines())}")
for b in bad:
    print("MISSING:", *b)
EOF
```

Then confirm the exports are fresh; the render is deterministic, so a clean
diff is the pass condition:

```bash
docs/diagrams/render.sh && git --no-pager diff --stat -- docs/diagrams/svg
```

Rendering requires the D2 CLI, pinned at `v0.9.0` (`brew install d2`).
`docs/diagrams/render.sh` renders every source with `--sketch`; the conventions
live in `.agents/skills/general/utrace-d2`.

## Skill Routing

Installed project skills live under `.agents/skills/`; `skills-lock.json` is
the canonical inventory. Route by the task's primary need, choose the smallest
non-overlapping set, and read every selected `SKILL.md` in full.

- **uTrace providers** (`utrace-providers/`): use the matching vendor skill for
  material work on Discord, OpenTelemetry, Stripe, Supabase, TypeSafe, or Vercel. Use repository provider skills and CLIs without requiring
  editor plugins or MCP servers. Read command help before relying on unfamiliar
  flags. The root npm package pins development tooling; after `npm ci`, run
  Stripe and Supabase CLI commands from the repository root with
  `npm run stripe -- <arguments>` and `npm run supabase -- <arguments>`.
  Use the Supabase skill for Supabase architecture and integrations.
  For the Claude API and the Claude Agent SDK, use the `claude-api` skill
  and Anthropic's documentation; for the voice agent, use the Pipecat, Daily,
  Deepgram, ElevenLabs, and OpenAI documentation.
  Use official vendor documentation when no installed skill fits. Treat the
  OpenTelemetry skills as vendor-neutral; they do not authorize Dash0 adoption.
- **General** (`general/`): use `gh-cli` for GitHub operations. For any work
  on uTrace documentation diagrams, read `utrace-d2` (project diagram
  conventions) — and when a genuinely new major diagram convention is
  established or an existing one changes, update that skill in the same
  change. For research, prefer official sources for technical claims.
- **Browser related** (`browser-related/`): use `agent-browser` for user-journey
  proof and Chrome DevTools skills for browser internals, extensions,
  accessibility, performance, or memory diagnostics.

Skills cannot override the PRD, architecture, user instructions, safety rules,
or authority for deployment and other external effects. Do not stack
equivalent workflow or review skills. When the inventory changes, update
`skills-lock.json`; do not use `npx skills list` as the router because it may
hide nested skills.

## Documentation Rules

- Follow the [Google developer documentation style guide](https://developers.google.com/style),
  including its guidance on headings, active voice, lists, links, and images.
  Preserve canonical uTrace terms. Use diagrams for relationships and structured
  flows, and prose for nuanced contracts; link to existing explanations instead
  of reproducing them.

- Put product architecture in `docs/ARCHITECTURE.md`; keep `docs/SCENARIO.md`
  to the scenario itself (journey, fixture, and acceptance) without
  implementation detail.
- Keep the lean active-provider lookup in `docs/ARCHITECTURE.md`; put detailed
  provider boundaries in the owning file under `docs/arch-components/`.
- One source of truth per fact. A diagram owns the relationships and flows it
  draws: the nodes, their grouping, and the labeled edges between them. Every
  enumerable list (state names, field lists, closed sets, rule bullets) lives
  in the markdown as code-formatted terms, never in a text-heavy card inside
  the diagram. The markdown does not repeat what the drawing already shows.
  - Good: "An Outreach Attempt moves `scheduled` → `dispatching` → `claimed`."
  - Bad: a gray "rules" card at the bottom of a diagram restating the prose.
- Whenever you edit documentation, check the diagrams it embeds — and any
  diagram whose content overlaps the edit — and update the affected `.d2`
  sources and re-rendered SVGs in the same change. Diagrams must never drift
  out of date from the prose they accompany.
- Never hand-edit `docs/diagrams/svg/*.svg`. Edit the paired `.d2` source,
  run `docs/diagrams/render.sh`, and commit both files together.
- Diagram edge labels are the connection's own label (`a -> b: label`), never
  floating text placed beside a line.

## Commits and PRs

- Never commit or push unless the user explicitly asks. Uncommitted changes
  are the user's review state — keep the diff clean and reviewable.
- Prefer `git merge` over squash unless the user asks otherwise.
- Subjects: short, imperative, focused on user impact. An area prefix in the
  style already used in history (`docs:`, `chore:`) is fine.
  - Good: `docs: standardize diagram label binding`
  - Bad: `updated some files`
- PR descriptions answer: what changed, why, and any PRD or architecture
  impact. Production merge is always human-owned.

## Boundaries

**Ask first:**

- Any edit to `docs/PRD.md` or `assets/`
- Removing or overwriting user work (treat dirty worktrees as user-owned)
- Reintroducing a deleted runtime stack or provider
- Commits, pushes, or destructive git operations

**Never:**

- Commit secrets, `.env*`, virtualenvs, dependency folders, build output,
  recordings, traces, or generated artifacts
- Hand-edit generated files when a generation workflow exists
- Skip deterministic local checks in favor of live service calls when a local
  check can answer the question
