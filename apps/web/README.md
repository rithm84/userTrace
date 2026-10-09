# uTrace Web

The Next.js application: the marketing landing page and the authenticated
product surfaces (the client dashboard, the Case File, and the uTrace session
tab).

## Local development

Use Node.js 24 and npm:

```sh
npm ci
npm run dev           # landing only, http://localhost:3000
npm run dev:product   # landing and product surfaces, http://localhost:3100
```

The landing needs no product API or authentication configuration. Development,
builds, and tests synchronize the seven canonical Rive characters and the
installed Rive WASM runtime into `public/agent/`.

The optional `LANDING_ONLY=1` setting redirects non-asset paths other than `/`
to the landing page, preserving the public site's existing redirect behavior.
Without it, missing routes return 404. `.env.example` lists every deployment
setting. Two local-only switches are not in it: `UTRACE_PARTICIPANT_FIXTURE`
(see [Product surfaces](#product-surfaces)) and `UTRACE_PREVIEW_DIST_DIR`, which
overrides the Next.js build directory.

The waitlist currently displays a confirmation for the current visit; it does
not store or send email addresses. Demo/contact links open email.

## Product surfaces

| Route | Who | What |
| --- | --- | --- |
| `/sign-in` | client staff | email one-time-code sign-in |
| `/sign-in/participant` | participants | Discord sign-in |
| `/auth/callback` | both | the single sign-in callback (PKCE code or email link) |
| `/dashboard` | staff | in-progress cases, Case Files and pull requests, Outreach Offers |
| `/dashboard/cases/[productSignalId]` | staff | one case end to end, with the deny-only **Stop this case** control |
| `/dashboard/settings` | staff | members, integration status, and the owner-only Outreach Offers allowance |
| `/case-files/[caseFileId]` | staff | the published Case File, sealed core apart from the updating projection |
| `/invitations/[token]` | participants | claim entry into an Interview Session |
| `/sessions/[sessionId]` | participants | the session tab: preflight, character, voice, controls, progress, preview, and the live transcript |
| `/extension/link` | participants | the extension account link: mints a one-time code and redirects to the extension |
| `/user-settings` | participants | the participant's account: their interviews and preview preferences |

Reads happen in Server Components and every mutation is a Server Action, so the
control plane's base URL and the caller's token stay server-side and the
browser only ever talks to this origin and to Supabase. `lib/api/schemas.ts` is
the web application's side of the control-plane HTTP contract, split by the
authority that serves each part: `schemas/lifecycle.ts` holds the shared domain
vocabulary, `schemas/staff.ts` the dashboard and Case File routes, and
`schemas/session.ts` the participant routes and the session projection. Every
response is parsed there before it reaches a component.

`apps/api/openapi.json` is the contract these mirror, and
`tests/product/domain-enums.test.ts` checks every lifecycle enumeration against
the JSON Schemas in `packages/utrace-contracts/schemas/domain`.

Two reads do not go through the control plane's HTTP API. The session tab's
projection is read from `public.session_projections` with the participant's own
Supabase session, which is the row Supabase Realtime then distributes under the
same row-level security. Sign-in itself is Supabase Auth.

The published Case File and its frequency/linked-report projection are two
separate requests, because they are two separate records: the core is sealed at
publication and the projection keeps updating after closure. The core names the
originating user and their organization by opaque client-scoped reference;
`GET /v1/clients/{id}/references` resolves those for authorized staff, and a
reference uTrace holds no recorded name for is rendered as itself.

`UTRACE_CONTROL_PLANE_FIXTURES=1` answers every control-plane call from the
Shopalytics fixture in `lib/api/fixture-data.ts`, which is what the route tests
and offline previews use. With it, the session tab replays a scripted
conversation into its transcript (`lib/session/fixture-voice.ts`) instead of
joining the voice agent's Daily call. Adding `UTRACE_PARTICIPANT_FIXTURE=1` admits the
fixture's participant without Discord sign-in; the `web-product-fixture-*`
entries in `.claude/launch.json` start either mode.

## Configuration

Copy `.env.example` to `.env.local` and fill it in. `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, and `NEXT_PUBLIC_UTRACE_EXTENSION_ID`, the
extension the session tab delivers its binding offer to, are browser-safe;
`UTRACE_API_BASE_URL` and `UTRACE_CLIENT_ID` are server-only and must never be
given a `NEXT_PUBLIC_` name. The Supabase secret key is not used by this
application at all.

### Enabling Discord sign-in

Participant sign-in needs the Discord provider switched on for the Supabase
project, which is dashboard configuration rather than repository code:

1. In the [Discord developer portal](https://discord.com/developers/applications),
   open the uTrace application and add
   `https://<project-ref>.supabase.co/auth/v1/callback` under **OAuth2 →
   Redirects**.
2. Copy the application's **Client ID** and **Client Secret**.
3. In the Supabase dashboard, open **Authentication → Providers → Discord**,
   enable it, paste both values, and save.
4. Under **Authentication → URL Configuration**, add this deployment's
   `/auth/callback` URL to the redirect allow list.

The repository holds the client id (`UTRACE_DISCORD_CLIENT_ID` in `.env.local`)
but not the client secret, so step 3 cannot be done from here.

## Verification

```sh
npx tsc --noEmit -p tsconfig.json
npx eslint .
npm test
npm run build
```

Two live checks are off by default.

The first renders the product surfaces against a running control plane with a
real staff access token, which proves the contract against the real database
rather than the fixture:

```sh
# a terminal for the API
cd ../api
set -a; source ../../.env.local; set +a
UTRACE_LANE_SIGNING_KEY=… UTRACE_FRONT_DOOR_SERVICE_TOKEN=… \
UTRACE_PREVIEW_ORIGIN_SUFFIX=preview.utrace.dev UTRACE_VOICE_AGENT_RUNTIME=in_process \
uv run uvicorn --factory app.main:create_default_app --port 8150

# and one for the check
set -a; source ../../.env.local; set +a
UTRACE_WEB_LIVE=1 UTRACE_API_BASE_URL=http://127.0.0.1:8150 \
UTRACE_CLIENT_ID=<client uuid> \
npx vitest run tests/product/dashboard.live.test.tsx
```

The second depends on the Discord provider configuration above:

```sh
set -a; source ../../.env.local; set +a
SUPABASE_DISCORD_OAUTH_ENABLED=1 npx vitest run tests/product/discord-oauth.live.test.ts
```

The shared character package also has its own checks:

```sh
npm --prefix ../../packages/utrace-agent-character test
npm --prefix ../../packages/utrace-agent-character run typecheck
```

## Source map

- `app/`: the landing route, the `(product)` route group, the sign-in callback,
  the root layout, and shared styles. `app/product.css` holds the product
  tokens; `docs/DESIGN.md` owns their values.
- `components/landing/`: landing interactions and simulated product screens.
- `components/product/`, `components/dashboard/`, `components/casefile/`,
  `components/session/`, `components/auth/`, `components/participant/`: the
  product surfaces.
- `components/brand/`: logo and wordmark paths.
- `lib/api/`: the control-plane contract, its HTTP client, and the fixture.
- `lib/auth/`, `lib/supabase/`: Supabase SSR clients, viewer resolution, route
  guards, and the sign-in actions.
- `lib/session/`: preflight, extension messaging, WebRTC voice, Realtime, and
  the session Server Actions.
- `public/`: fonts, logos, artwork, and animation exports.
- `scripts/`: character asset synchronization.
- [`utrace-agent-character`](../../packages/utrace-agent-character/): shared
  character runtime, seven canonical characters, and Rive authoring tools.

[Product documentation](../../docs/ARCHITECTURE.md) owns the architecture these
surfaces implement.
