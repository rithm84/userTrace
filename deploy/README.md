# Deployment

How the always-on parts of uTrace are deployed, and the exact commands that
build, release, and roll them back. The architecture this serves is
[deployment and network topology](../docs/arch-components/deployment.md); this
file is the operational counterpart, not a second copy of it.

## Current state

The `api` and `worker` computes run in the uTrace Supabase project
(`gjhxlzeirymgwqqxvzlf`, `us-west-2`), and the API answers `/health` at
`https://api.utrace.dev/compute/v1/api/health`, the project's active custom
domain. The `utrace-preview-front-door` production environment is configured;
`vercel deploy --prod` from a checkout linked to that project releases it.

## Topology

| Workload | Where it runs | Owned by |
| --- | --- | --- |
| Control-plane API | Supabase Compute `api`, public | [`compute/deploy.py`](./compute/deploy.py), `[compute.api]` in `supabase/config.toml` |
| Control-plane worker | Supabase Compute `worker`, private, one instance | [`compute/deploy.py`](./compute/deploy.py), `[compute.worker]` in `supabase/config.toml` |
| Preview front door | Vercel project `utrace-preview-front-door` | `apps/preview-front-door`, configured here |
| Web surfaces | Vercel project `utrace-web`, serving `utrace.dev` | `apps/web`; deploys automatically from `main` of `rithm84/userTrace` |
| Implementation workspaces, preview runtimes | Vercel Sandbox | `vercel/` in this directory |
| Postgres, Storage, Realtime, Queues, Cron | Supabase | `supabase/` at the repository root |

Supabase Compute is in private alpha, and the CLI marks `supabase compute` as
experimental and subject to change without notice.

Both computes build `apps/api/Dockerfile` from the same commit. The image's
command runs `uvicorn --factory app.main:create_default_app` on `$PORT`; the
worker's staged copy of the Dockerfile appends `CMD ["utrace-worker"]`, because a
compute has no per-compute setting that chooses an entry point. Compute's launcher
wraps that command and serves whatever listens on `$PORT`. Both computes use the
`2gb` size (2 GB of memory, 1 vCPU).

`supabase/config.toml` declares each compute's runtime, size, exposure, and build
exclusions (`.env`, `.env.*`, and `.git`), so nothing about the computes is set by
hand. Its `[remotes.production]` block holds the hosted project's Auth, pooler,
and Storage settings, which `supabase config push --project-ref <ref>` applies.

### The worker holds the Discord gateway

`utrace-worker` runs three things in one process: the Job loop, one
watched-channel consumer per configured client, and a liveness listener. The
consumers are what put a customer's Discord message into intake, so **the worker
compute is the Customer Surface connection**, not just a queue drainer. The
[liveness listener](../apps/api/app/execution/liveness.py) answers every request
on `$PORT` with `200 ok`, because the worker has no API of its own; it carries no
state or authority. Any of the three ending stops the others.

That makes its single instance a correctness constraint rather than a cost
choice. Two instances would hold two gateway sessions for one bot token and
deliver every watched-channel message twice. `[compute.worker]` names no
`instances`, so a push keeps the recorded count, which defaults to one. Never push
the worker with `--instances` greater than `1`.

On SIGTERM the worker lets the Job in flight finish and closes the gateway
sessions within the consumers' shutdown window
(`UTRACE_INTAKE_CONSUMER_SHUTDOWN_SECONDS`, ten seconds). Intake can miss messages
while the worker is replaced during a push; the staff route below recovers them.

A consumer that loses its gateway session retries with warnings and jittered
backoff. After `UTRACE_INTAKE_CONSUMER_MAX_ATTEMPTS` it records
`intake_consumer_failed` and the process exits non-zero, so Compute restarts the
worker rather than leaving one that no longer hears the Customer Surface.

Messages posted while no consumer was connected are recovered per message, by a
client owner or admin, with
`POST /v1/clients/{client_id}/intake/messages` and a
`{"channel_ref": ..., "provider_message_id": ...}` body. It re-reads that
message through the connector and runs it through the consumer's own path;
repeating it is idempotent.

The consumers read each client's watched channels from its active configuration
release when they start, so publishing a release that changes the watched
channels takes effect at the next worker release.

## Commands

All commands run from the repository root with the repository `.env.local`
present. The scripts read it; they never print a value from it. The Supabase CLI
commands below take the project reference from the same file:

```sh
set -a; source .env.local; set +a
```

### Release order

A release runs three steps in this order, each one only when its input changed:

1. Database migrations, because new code may depend on the new schema, and
   schema changes are expand-and-contract, so the running code keeps working on
   it.
2. `deploy.py secrets`, so the computes start with the new environment.
3. `deploy.py push`.

### Apply migrations

```sh
set -a; source .env.local; set +a
uv run --project apps/api python apps/api/scripts/apply_migrations.py
```

The repository's runner applies each unapplied migration in order and records it,
the one path [Durable data and schema](../docs/arch-components/data-and-schema.md#migrations)
allows. `UTRACE_DATABASE_URL` in `.env.local` is the session pooler connection
string: Supabase's direct database host is IPv6-only without the IPv4 add-on, so
workstation tools use the pooler.

### Sync the runtime environment

```sh
uv run deploy/compute/deploy.py secrets
```

Writes the control plane's runtime environment as project secrets, which Compute
injects into every compute as environment variables. It removes any secret the
environment no longer names, leaves the `SUPABASE_`-prefixed values Compute
injects itself, then reads back each secret's digest and refuses a mismatch. It
prints secret names and counts only. The values come from `.env.local`, apart
from the deployment policy described in
[values the deployment supplies](#values-the-deployment-supplies).

The script mints nothing: `UTRACE_LANE_SIGNING_KEY` and
`UTRACE_FRONT_DOOR_SERVICE_TOKEN`, which are uTrace's own, must already be in
`.env.local`. `UTRACE_FRONT_DOOR_SERVICE_TOKEN` must equal
`UTRACE_CONTROL_PLANE_SERVICE_TOKEN` in the Vercel front-door project, so rotating
it means changing both in the same change.

### Build and deploy the computes

```sh
uv run deploy/compute/deploy.py push
```

Refuses a dirty working tree, so a deploy always names committed bytes. It stages
a `git archive` of HEAD, limited to the files the Dockerfile copies, into
`supabase/compute/<name>/` (gitignored), labels each Dockerfile with the commit,
appends the worker's command to its copy, and runs `supabase compute push api
worker`. Supabase builds both images server-side; the script fails unless both
builds become `active`, then waits for `/health` on the project URL.

### Inspect and read logs

```sh
npm run supabase -- compute status worker --project-ref "$SUPABASE_PROJECT_REF"
npm run supabase -- compute logs worker --kind app --follow --project-ref "$SUPABASE_PROJECT_REF"
```

`status` shows a compute's build state, size, access, image, and live instance
count. `logs` prints the last 24 hours of a compute's own output (`app`), the HTTP
requests it served (`requests`), and its deploy lifecycle (`builds`); `--follow`
polls every 10 seconds. The Supabase dashboard shows the same logs.

### Custom domain and DNS

`api.utrace.dev` is the project's custom domain, added through the Custom Domain
add-on; the [network contract](../docs/arch-components/deployment.md#network-and-public-boundaries)
describes what it serves and how the API's base URL carries a path. `utrace.dev`
uses Vercel DNS, so any record Supabase asks for during activation is added with
`vercel dns add utrace.dev <name> <type> <value>`.

`utrace.dev` publishes explicit CAA records for `letsencrypt.org`, `pki.goog`,
and `sectigo.com`. Vercel's default set is replaced the moment a custom record is
added, so the certificate authority that issues the custom domain's certificate
must be one of them.

### Front door

The project's root directory is `apps/preview-front-door`, so a deployment runs
from the repository root, where `.vercel/project.json` links it:

```sh
vercel env ls production           # nine variables, the service token sensitive
vercel deploy --prod --yes
```

`UTRACE_CONTROL_PLANE_BASE_URL` is the API base URL including its path,
`https://api.utrace.dev/compute/v1/api`.

`UTRACE_UPSTREAM_APPLICATION_PORT` is `3300`, the application port in
[`deploy/vercel/preview/shopalytics-recipe.json`](./vercel/preview/shopalytics-recipe.json),
not the `3000` in the app's `.env.example`. The door refuses to route to a port
the resolved route does not name, so this value and the recipe move together.

### Roll back

Compute has no rollback command. Roll back by pushing an earlier commit from a
clean tree:

```sh
git switch --detach <previous git commit sha>
uv run deploy/compute/deploy.py push
git switch -
```

The earlier commit must contain `deploy/compute/deploy.py`. The project secrets
stay as last synced; if the environment changed since that commit, run
`deploy.py secrets` from the same checkout before `push`. Migrations are not
rolled back: expand-and-contract keeps the earlier code working on the newer
schema. To roll back the front door: `vercel rollback --scope rithm84s-projects`.

`npm run supabase -- compute delete <name> --project-ref "$SUPABASE_PROJECT_REF"`
removes a compute entirely. It is irreversible; the CLI keeps the staged
directory and the `supabase/config.toml` entry.

## Values the deployment supplies

[`compute/deploy.py`](./compute/deploy.py) names every runtime variable. Most
are copied from `.env.local`. These are decided in the script, in review, because
they are facts about the deployment rather than about a developer's machine:

| Variable | Value | Why |
| --- | --- | --- |
| `UTRACE_DATABASE_URL` | `.env.local`'s `UTRACE_DATABASE_DIRECT_URL` | The computes reach Postgres directly inside Supabase's network, so the session pooler's client limit does not bind them. |
| `UTRACE_DB_POOL_MAX` | `6` | Each compute's pool cap; listeners hold connections outside it. |
| `UTRACE_WEB_ORIGIN` | `https://utrace.dev` | `.env.local` carries a local origin; Session Links must be public. |
| `UTRACE_INGEST_WEBSOCKET_ORIGIN`, `UTRACE_PREVIEW_SERVER_CONTROL_URL` | `wss://api.utrace.dev/compute/v1/api` | The ingestion edge and the Server SDK control channel are served by this API. |
| `UTRACE_PREVIEW_SERVER_INGEST_URL` | `https://api.utrace.dev/compute/v1/api` | The Server SDK exports to this API. |
| `UTRACE_PREVIEW_ORIGIN_SUFFIX` | `preview.utrace.dev` | The preview apex the front door serves. |

The rest of the policy (preview TTLs and timeouts, intake consumer retries) is
listed in the script. Compute injects `SUPABASE_URL` and refuses secrets whose
names start with `SUPABASE_`, so the control plane reads its Supabase keys as
`UTRACE_SUPABASE_SECRET_KEY` and `UTRACE_SUPABASE_PUBLISHABLE_KEY`. The GitHub App
private key is the `UTRACE_GITHUB_PRIVATE_KEY` secret.

The preview template is built from a workstation with
`apps/api/scripts/build_preview_template.py`, so its fixture-checkout and
registry variables are never part of the deployed computes.

## Production checklist

1. Apply migrations if any changed.
2. `uv run deploy/compute/deploy.py secrets` if any runtime value changed.
3. `uv run deploy/compute/deploy.py push` from a clean tree.
4. `curl -fsS https://api.utrace.dev/compute/v1/api/health` answers
   `{"status":"ok"}`.
5. The worker's `app` logs show `worker_ready` with its registered job types,
   then `intake_consumers_started` and one `intake_consumer_subscribed` per
   configured client naming its watched channels.
6. An unauthenticated WebSocket upgrade to `/v1/ingest/browser` under the base URL
   closes 1008 with a `credential_rejected` error frame — the API's refusal, not
   the platform's.
7. `vercel deploy --prod` for the front door, then a bare session origin
   returns the neutral interstitial and a claim with a bad code is refused.
8. `preview.utrace.dev` shares the `utrace.dev` site, which the
   [Shopalytics demo](../docs/SCENARIO.md#starting-state-and-fixture) accepts only
   because Shopalytics is a uTrace-owned fixture.

## Constraints this deployment is built around

- **Secrets are project-scoped.** Every compute receives every project secret
  (see [secrets and credentials](../docs/arch-components/deployment.md#secrets-and-credentials)),
  so keep only control-plane credentials in the project's secrets.
- **The `SUPABASE_` prefix is reserved.** Compute injects those names itself and
  refuses project secrets that use it.
- **One worker instance.** See
  [the worker holds the Discord gateway](#the-worker-holds-the-discord-gateway).
- **A prefixed base URL.** The API is reachable only under `/compute/v1/api`, so
  every client is configured with the full base URL, not the bare origin.
