# uTrace control plane

Python 3.13+ FastAPI API and durable workers, managed with `uv`. Module ownership
follows [control plane internals](../../docs/arch-components/control-plane.md#runtime-shape-and-modules).

## Layout

```
app/settings.py             required configuration; an unset variable fails start-up
app/ids.py                  UUIDv7 generation (identifiers are minted in Python)
app/errors.py               the typed refusals every layer raises
app/envelope.py             commit-time intents the kernel writes, built on `utrace_contracts.envelope`
app/db/                     Postgres connector, typed row accessors, transition kernel
app/lifecycle/              pure state machines plus the controllers that commit them
app/execution/              Jobs, Job Runs, Commands, outbox, inbox, worker, recovery
app/auth/                   Supabase JWT verification, lane credentials, dependencies
app/ports/                  provider seams the provider adapters implement
app/adapters/               ports one area fills from another's durable state
app/api/                    routers, typed responses, and the error contract
app/interview/              voice runtime, readiness, character and session projections,
                            participant controls, preflight, evidence, Jev attention
app/context/                revisioned source-linked context bundles and their freshness
app/extension/              installation identity, the control socket, bindings, commands
app/queries.py              tenant-scoped read projections
```

## Commands

```sh
uv sync                                   # install into apps/api/.venv
uv run pytest                             # integration tests; local database only, see Configuration
uv run ruff check .                        # lint
uv run mypy app                            # strict type check
uv run python -m scripts.export_openapi     # regenerate openapi.json
```

Database migrations live in [`supabase/migrations`](../../supabase/migrations) and
apply from the repository root with the repository's own runner, which records
each file in `supabase_migrations.schema_migrations` and never resets a project:

```sh
set -a; source .env.local; set +a
uv run --project apps/api python apps/api/scripts/apply_migrations.py
```

Schema migrations are generated from the declarative schema in
[`supabase/schemas`](../../supabase/schemas);
[Durable data and schema](../../docs/arch-components/data-and-schema.md#migrations)
describes how to change it.

## Running

The application is a factory so tools can import it without credentials:

```sh
UTRACE_VOICE_AGENT_RUNTIME=in_process uv run uvicorn --factory app.main:create_default_app
uv run utrace-worker
```

`create_app(settings, ports)` takes a `PortRegistry`; a provider adapter installs
its implementations there. A route whose port is not registered raises
`NotYetWiredError`, a typed `501` naming the missing capability, rather than
returning a silent stub.

Two seams are filled from inside this application rather than by a provider.
`app/adapters/` implements the preview area's `ObservationReadinessPort` and the
validation area's evidence query over the telemetry-ingestion stream's durable
state, and `create_app` installs them once the database pool is open.

## Configuration

The settings classes are the source of truth for required variables; start from
[`.env.example`](./.env.example).

Database-backed tests truncate every owned table in whatever
`UTRACE_DATABASE_URL` names. Run them only against the local Supabase stack, with
`UTRACE_DATABASE_URL` and `SUPABASE_URL` pointing at it; never with `.env.local`
loaded as is. `tests/conftest.py` fills any variable the environment leaves unset
from the repository `.env.local`, so export both before running. A test whose
variable is missing skips with the variable named.

## Server SDK control channel

`WS /v1/server-sdk/control` is exercised against the real `@utrace/server-sdk`:
`tests/ingestion/test_server_control.py` builds the package, starts the Express
service in `tests/ingestion/server_sdk/` under `node --import`, and observes the
activation, export, probe, rotation, and control commands it produces. Node 22.12
or later is the only extra prerequisite; the build and the package install run on
first use. The service reads its rows from the `utrace_ingestion_control_test`
schema in the same database, so the local-database rule under
[Configuration](#configuration) applies here too.

```sh
uv run pytest tests/ingestion tests/adapters
```

## Connectors

`app/connectors/` holds one class per provider role in the
[active provider lookup](../../docs/ARCHITECTURE.md#active-provider-lookup); the roles a
caller must not bind to one vendor sit behind the protocols in
`app/connectors/ports.py`. Every provider error becomes a typed `ConnectorFailure`; an
unmapped error fails closed.

`tests/connectors/` mixes pure tests with live smoke tests against the real providers.
There are no mocks. Each live test reads its credentials from the repository `.env.local`
and skips with a reason naming the exact variable that is missing.

```sh
uv run pytest tests/connectors
uv run ruff check app/connectors tests/connectors && uv run mypy app/connectors
```

Environment variables the connectors and their smokes read:

| Provider | Variables |
| --- | --- |
| OpenAI API (GPT-6.1 Sol for the voice agent and every reasoning role) | `OPENAI_API_KEY` |
| Voice agent (Pipecat on Daily, Deepgram Flux, ElevenLabs) | `DAILY_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `UTRACE_VOICE_AGENT_RUNTIME` (`pipecat_cloud` in deployment, `in_process` locally); `PIPECAT_CLOUD_PUBLIC_API_KEY` only when the runtime is `pipecat_cloud`; optional `UTRACE_VOICE_ID` and the other `UTRACE_VOICE_*` overrides in `app/interview/settings.py` |
| Vercel AI Gateway (Jev) | `VERCEL_AI_GATEWAY_API_KEY`, `UTRACE_JEV_MODEL_ID`, `UTRACE_JEV_TIMEOUT_SECONDS` |
| Vercel Sandbox | `UTRACE_VERCEL_SANDBOX_TOKEN`, `UTRACE_VERCEL_TEAM_ID`, `UTRACE_VERCEL_PROJECT_ID`; `UTRACE_IMPLEMENTATION_WORKSPACE_SNAPSHOT_ID` for the client seed script only |
| Stripe sandbox | `STRIPE_SANDBOX_SECRET_KEY`, `UTRACE_STRIPE_SMOKE_CUSTOMER_ID` |
| Discord | `UTRACE_DISCORD_BOT_TOKEN`, `UTRACE_DISCORD_SERVER_ID`, `UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID`, `UTRACE_DISCORD_DM_TEST_USER_ID`, `UTRACE_DISCORD_SMOKE_MESSAGE_ID`, `UTRACE_DISCORD_ALLOW_TEST_DM` |
| GitHub App | `UTRACE_GITHUB_APP_ID`, `UTRACE_GITHUB_INSTALLATION_ID`, `UTRACE_GITHUB_PRIVATE_KEY`; `UTRACE_GITHUB_OWNER`, `UTRACE_GITHUB_REPOSITORY` (smokes) |

`UTRACE_DISCORD_ALLOW_TEST_DM=1` opts in to the one smoke that delivers a real direct
message; it is skipped otherwise. The GitHub App is app id `4579213`; its private key is
`UTRACE_GITHUB_PRIVATE_KEY` in `.env.local`, and a project secret on Supabase Compute.
