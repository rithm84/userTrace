# Client configuration

[Back to uTrace architecture](../ARCHITECTURE.md)

The demo's scripted configuration becomes the release every Product Signal pins:

![Configuration scripts publish an immutable release that the demo harness verifies and each Product Signal pins.](../diagrams/svg/client-configuration.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Configuration ownership

uTrace configures the demo's client with scripts rather than a self-service flow.
Client [staff authority](./identity-and-access.md#authorization-boundaries) is
limited to stopping a case and, for the owner, authorizing the Outreach Offers
budget. A product may use several services, but each implementation stays within
one authorized repository.

## Immutable configuration releases

Publishing a configuration atomically creates an immutable
`ClientConfigurationRelease`. Every Product Signal pins one release by identity
for its lifecycle, so later releases govern only new signals. Offboarding,
credential revocation, and platform security prohibitions are immediate deny-only
overrides.

### Numbering and digest

A release never changes, so it has no lifecycle state. The database, not
convention, keeps releases append-only: a client's releases are numbered, the
highest is active, and none is retired, superseded in place, or deleted.

Each release carries a `sha256:` digest over its policy's canonical
serialization, an identity for comparison rather than a second copy. Equal
digests mean equal policy, so publication treats a re-publish as a no-op, and a
dashboard or SDK can compare what it holds with what the case pinned.

### Release contents

A release pins:

- the Customer Surface: the Discord installation and its watched channels
- the implementation policy, which is the Autonomy Policy the
  [Implementation Gate](./policy-and-permissions.md#autonomy-policy-model) narrows
- the data seeding contract: seed manifest digest, fixture clock, and preview
  template
- the Outreach Offers policy and the private-contact budget
- the Product Overview digest
- the annotation manifest and the signed server instrumentation manifest
- the dashboard origin a draft PR links its Case File from

The client compatibility contract explicitly declares repository, environment,
startup, health, and seeding requirements; uTrace never infers an arbitrary
application stack or invents startup and health behavior from repository
contents.

A release carries no model configuration: the GPT-6.1 Sol, Jev, and voice
provider settings in the [model access contract](./deployment.md#model-access)
are platform-wide uTrace integrations, not provider accounts the client or
originating user must supply.

## SDK source integration

The client adds browser and server SDK dependencies, preview entry points,
product annotations, and build and startup configuration through its normal
code-review process, and merges them into its maintained application branch,
usually `main`. This client-owned setup is not a product Implementation Attempt,
and no permanent divergent uTrace branch is part of it.

The same source revision supports distinct production and preview profiles:

- The preview profile loads the browser bootstrap before application hydration
  and the server runtime before backend modules, then requires authenticated
  preview activation.
- The production profile excludes active uTrace runtime code and startup hooks.
  Retained annotation helpers are inert and must not affect business behavior.

Preparing the configuration checks both profiles: complete correlated
observation in a prepared preview, and no active uTrace runtime, hooks,
connections, or exports in a production build and startup smoke check.
Dependency installation alone never activates collection. The
[browser SDK](./browser-sdk.md#package-and-bootstrap-boundary) and
[server SDK](./server-sdk.md#preview-only-ownership-and-startup) own their
loading contracts.

## Verification by the demo harness

The demo harness's `demo prepare` brings a target control plane to the
[scenario's](../SCENARIO.md#starting-state-and-fixture) starting state and
verifies it before a run; a configuration change publishes a new release and
reruns `demo prepare`.

Configuration verification covers platform compatibility, not a participant's
session: each encounter must satisfy the
[conversation and observation readiness boundaries](./interview-and-voice.md#readiness-has-two-boundaries),
including a compatible extension and the session-specific binding.

Long-lived provider credentials live in the control plane's
[credential custody](./deployment.md#secrets-and-credentials); release manifests
contain scoped references only.
