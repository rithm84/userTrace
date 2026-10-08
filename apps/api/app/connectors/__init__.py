"""Provider adapters for the uTrace control plane.

One class per architectural role in the
[active provider lookup](../../../../docs/ARCHITECTURE.md#active-provider-lookup):

| Role | Class |
| --- | --- |
| Delegation answers, gate proposals, intake assistance, Case File synthesis | `OpenAIResponsesGateway` |
| Full-duplex conversational voice | `GptLiveSessions` |
| Default intake decisions and concurrent semantic attention | `JevClient` |
| Isolated implementation and preview compute | `VercelSandboxes` |
| Branches, diffs, and PRs | `GitHubAppDelivery` |
| Outreach Offers invoice credits | `StripeCredits` |
| Customer Surface | `DiscordConnector` |

Shared rules for every connector here:

- Callers import each connector from its own module; this package re-exports nothing.
- Every provider error becomes a `ConnectorFailure` from `failures`, carrying a closed
  technical category, retry advice, and effect state. An unmapped error fails closed.
- Every provider payload uTrace consumes is validated at runtime by a pydantic model that
  requires the fields uTrace needs and ignores unrelated extras.
- A connector executes an already-authorized request. It never decides authority, never
  creates evidence, and never advances lifecycle state.
- Retries happen only where idempotency or proof of no side effect makes them safe, through
  `retry.retry_safe_call`.
"""
