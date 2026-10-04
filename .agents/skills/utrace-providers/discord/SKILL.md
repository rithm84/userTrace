---
name: discord
description: >
  Work on uTrace's Discord Customer Surface: bot token custody, gateway intents
  and the one-session-per-token constraint, watched-channel configuration from
  the pinned configuration release, the receipt path into Intake Items, private
  DM outreach, the operator re-ingest route, rate limits, and the live tests.
  Use when changing watched-channel intake, the gateway consumer, private
  invitation or re-contact delivery, inbound direct-message routing, or the
  Discord connector itself.
---

# Discord Skill

> One configured Discord server per uTrace Client. Watched channels are the only
> intake surface; direct messages are private outreach.

## Scope

Discord is the pilot's only [Customer Surface](../../../../docs/PRD.md#customer-surfaces).
The [intake and outreach contract](../../../../docs/arch-components/signal-and-outreach.md#discord-ingestion-and-identity)
owns the rules; this skill maps them to the code.

| Concern | Where it lives |
| --- | --- |
| Provider boundary | `apps/api/app/connectors/discord_connector.py` |
| Typed receipts the rest of uTrace sees | `apps/api/app/connectors/ports.py` |
| Watched-channel consumer | `apps/api/app/intake/worker.py` |
| Consumer supervision and process placement | `apps/api/app/intake/runtime.py` |
| Receipt to Intake Item | `apps/api/app/intake/messages.py` |
| Private outreach dispatch | `apps/api/app/outreach/dispatch.py`, `delivery.py` |
| Inbound direct-message routing | `apps/api/app/outreach/inbound.py` |
| Operator re-ingest route | `apps/api/app/api/intake.py` |
| Live smokes | `apps/api/tests/connectors/test_discord_live.py`, `apps/api/tests/workstream.py` |

## Library choice

`discord.py>=2.7`, pinned in `apps/api/pyproject.toml`. It owns the identify and
resume handshake, heartbeat and zombied-connection detection, session
invalidation, gateway rate limits, and REST bucket accounting. Do not hand-roll a
gateway client, and do not let a `discord.Message` escape the connector: callers
receive `MessageReceipt` and `DirectMessageReceipt` from
`app.connectors.ports`.

## Bot token custody

One bot token serves the deployment. It is never in a release manifest, a
database row, or a log line.

- Production: AWS Secrets Manager, injected as `UTRACE_DISCORD_BOT_TOKEN` through
  the task definition's `ValueFrom` (`deploy/control-plane/platform.yaml`).
- Local and tests: `UTRACE_DISCORD_BOT_TOKEN` in `.env.local`.
- Read in code only through `IntakeOutreachSettings.discord_bot_token`
  (`apps/api/app/intake/settings.py`).
- `redacted_parameters` in `apps/api/app/connectors/failures.py` replaces any
  value whose key ends in `_token`, `_key`, or `_secret`, which is why a
  `ConnectorFailure` can carry request parameters safely. Keep new parameter
  names inside that convention.

`DiscordConnector.open()` logs in over REST only. REST login is enough to fetch
identities, channels, and messages, and to send a direct message; opening the
gateway is the separate explicit `consume()` step. A call before `open()` is
refused with `TechnicalCategory.CONFIGURATION` rather than failing inside the
provider library.

## Gateway intents

`DiscordConnector.__init__` starts from `discord.Intents.none()` and enables
exactly four:

| Intent | Why |
| --- | --- |
| `guilds` | resolve the configured guild and its channels |
| `guild_messages` | observe watched-channel messages |
| `message_content` | **privileged** — without it message bodies arrive empty |
| `dm_messages` | receive inbound direct messages for `app.outreach.inbound` |

`message_content` must be enabled for the application in the Discord developer
portal. `consume()` checks `self._client.intents.message_content` before
connecting and raises a `CONFIGURATION` failure if it is off, because intake
would otherwise route empty bodies. Never widen the intent set to get a
convenience: the narrow set is the reason the bot sees nothing outside its
configured surface.

## One gateway session per token

A second gateway session on the same token duplicates every receipt. The
consumer therefore lives inside the single `utrace-worker` ECS task, beside the
Job loop, and that service is pinned to one task whose deployment replaces rather
than doubles it (`apps/api/app/intake/runtime.py` explains the choice). Two
consequences to respect:

- The worker service cannot scale past one task while it hosts the gateway.
- Do not start a `WatchedChannelConsumers` run anywhere a second copy could run
  concurrently — including a local worker pointed at a database whose deployed
  worker is already consuming.

`WatchedChannelConsumers` runs one supervised consumer per client with a
configured gateway session, all sharing the one token. A consumer that loses its
session retries with a warning and bounded jittered backoff
(`UTRACE_INTAKE_CONSUMER_MAX_ATTEMPTS`, `UTRACE_INTAKE_CONSUMER_BACKOFF_SECONDS`,
`UTRACE_INTAKE_CONSUMER_HEALTHY_SECONDS`, `UTRACE_INTAKE_CONSUMER_SHUTDOWN_SECONDS`);
after the last attempt the supervisor stops the others and returns the typed
failure, and the worker exits non-zero so ECS replaces the task. Nothing is
recovered silently.

## Watched channels come from the pinned release

`connector_for_release` in `apps/api/app/intake/worker.py` builds the connector
from `release.policy.customer_surface`: `installation_ref` is the guild id and
each `watched_channels[].channel_ref` is a channel id. The set is fixed at
construction, so intake surfaces are configuration, never a runtime decision,
and a new release reaches the consumer when the process restarts.

Filtering happens at the boundary in `_WatchingClient.on_message`: another
guild, an unwatched channel, or the bot's own message never becomes a receipt.
`CustomerSurfaceConfig.watches(channel_ref)` is the same check for callers that
need it before touching the provider.

## The receipt path

```
gateway on_message      →  MessageReceipt
  →  record_message()       -- app/intake/messages.py
  →  seal_batch()           -- ordered immutable Intake Item
  →  route_intake_item_job  -- Jev classification, then outreach
```

- The authenticated Discord `message_id` is the provider idempotency key. A
  duplicate gateway delivery collides on the unique index and changes nothing;
  `RecordedMessage.duplicate` reports it.
- Messages from one author in one watched channel collapse into one open batch,
  which seals after `UTRACE_INTAKE_DEBOUNCE_QUIET_SECONDS` of quiet or at
  `UTRACE_INTAKE_DEBOUNCE_MAX_SECONDS`.
- `MessageReceipt` preserves `reply_to_message_id` and `thread_id`, so routing
  context assembles without a second provider read.
- A bot-authored message is never a customer report. It is accepted only when an
  explicit client-scoped attribution names the reporting client user, which is
  how a test reuses a bot-posted fixture message.
- Three refusals are boundary outcomes, not consumer failures: an unwatched
  surface (`UnwatchedSurfaceError`), an unattributed bot author
  (`UnattributedAuthorError`), and an author with no client-scoped identity.
  Each is logged with its reason and the next receipt is read.
- `scoped_provider_identity(guild_id=..., user_id=...)` yields
  `discord:<guild>:<user>`. It is guild-scoped because participation is scoped
  per client, and it never substitutes for a `client_user_id`.
- The receipt queue holds 1000 entries; an overflow logs
  `discord.receipt.dropped` and the message is recovered through the re-ingest
  route below.

## Private direct-message outreach

Invitations and validation re-contact are private DMs bound to the originating
user's Discord identity (`app/outreach/dispatch.py`). The Command
`outreach.send_direct_message` is committed before the provider call, and
`send_direct_message` is **never retried**: Discord exposes no idempotency key
for a message send, so a repeat would deliver a second invitation. A later
dispatch that finds an attempt still `dispatching` with a dispatched command and
no recorded delivery raises `UncertainDeliveryError` instead of sending again;
reconciliation reads the direct-message channel before any resend.

`DirectMessageReceipt` is a delivery result. It is not proof the person read the
message and never a Product Signal state change.

Two refusals are distinct:

- `direct_message_allowlist` is configured and the recipient is outside it →
  `AUTHORIZATION` failure raised **before** Discord is called. Production passes
  `direct_message_allowlist=None`; tests pass exactly one id.
- `discord.Forbidden` → the recipient does not accept DMs from this bot.

Inbound DMs route only to an invitation, an active Interview Session, a
re-contact, or a closed-vocabulary preference action such as an opt-out
(`app/outreach/inbound.py`). A direct message is not an intake surface and can
never become a Product Signal.

### `UTRACE_DISCORD_ALLOW_TEST_DM`

Delivering a real message to a real person is opt-in. Both
`tests/connectors/test_discord_live.py` and
`tests.workstream.require_dm_opt_in()` skip unless
`UTRACE_DISCORD_ALLOW_TEST_DM=1`. The test connector is additionally built with
an allowlist holding only `UTRACE_DISCORD_DM_TEST_USER_ID`, so even a mistaken
recipient argument is refused locally. Do not set the variable to make a suite
green; set it deliberately, knowing a Discord account receives a message.

## Operator re-ingest route

`POST /v1/clients/{client_id}/intake/messages` with
`{"channel_ref": ..., "provider_message_id": ...}` re-reads one message and puts
it through intake. It is the recovery path for a message the consumer never saw,
such as one posted while the worker was restarting.

- Staff-authorized (`StaffOfPath`) for the client in the path.
- A channel the client's active release does not watch is refused **before**
  Discord is called, so the route cannot read a non-intake channel.
- Content, author, and thread references come from Discord, not the caller.
- It seals the batch inline so the response can carry the Intake Item identity;
  a batch still open for that author and channel therefore seals now.
- Re-ingesting the same `provider_message_id` is idempotent and returns the
  original Intake Item with `duplicate: true`.

## Rate limits and typed failures

`discord.py` handles REST bucket accounting and gateway rate limits internally,
so this connector does not implement its own backoff. What it does is translate
outcomes: every provider error becomes a `ConnectorFailure` carrying a
`FailureDetail` with a `TechnicalCategory`, a `RetryAdvice`, an `EffectState`,
redacted request parameters, the status code, and the provider error class.

`technical_category_for_status` (`app/connectors/failures.py`) maps 429 to
`RATE_LIMITED`, 401 to `AUTHENTICATION`, 402/403 to `AUTHORIZATION`, 404 to
`NOT_FOUND`, 500/502/503/529 to `PROVIDER_UNAVAILABLE`, and anything unmapped to
`INTERNAL`, which fails closed. Override `default_retry_advice` only with
provider-specific proof, such as a response naming a retry-after window. A
gateway connection that ends on its own becomes a `PROVIDER_UNAVAILABLE` failure
with `RetryAdvice.AFTER_DELAY`, which is what makes the consumer's stall
visible instead of silent.

## Live tests

There are no mocked Discord tests. The smokes run against the real bot, guild,
and channel.

Required variables (`apps/api/README.md` lists them all):

| Variable | Use |
| --- | --- |
| `UTRACE_DISCORD_BOT_TOKEN` | bot authentication |
| `UTRACE_DISCORD_SERVER_ID` | the configured guild |
| `UTRACE_DISCORD_FEATURE_REQUEST_CHANNEL_ID` | the watched channel |
| `UTRACE_DISCORD_DM_TEST_USER_ID` | the only allowed DM recipient |
| `UTRACE_DISCORD_SMOKE_MESSAGE_ID` | optional; enables the `fetch_message` smoke |
| `UTRACE_DISCORD_ALLOW_TEST_DM` | `1` opts in to one real DM |

```bash
cd apps/api
set -a; source ../../.env.local; set +a
uv run pytest -q tests/connectors/test_discord_live.py
```

`tests/connectors/test_discord_live.py` proves the bot identity and scoped
provider key, that the configured watched channel exists in the configured
server, that a recipient outside the allowlist is refused before any send, that
a call before `open()` is refused, and — opt-in — one real direct message.

`tests/workstream.py` drives the end-to-end intake path: `post_fixture_message`
posts in the watched channel as the bot (the only account a test can speak as),
`attribute_author` gives that bot identity a reporting client user so intake may
accept it, then `record_message` and `seal_batch` produce the Intake Item.

## Links

- [discord.py documentation](https://discordpy.readthedocs.io/en/stable/)
- [Gateway intents](https://discord.com/developers/docs/events/gateway#gateway-intents)
- [Rate limits](https://discord.com/developers/docs/topics/rate-limits)
- [Developer portal](https://discord.com/developers/applications)
