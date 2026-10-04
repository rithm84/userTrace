# Preview runtime template

The pinned Vercel Sandbox template every Shopalytics preview runtime is created
from. `shopalytics-recipe.json` is the committed description;
`apps/api/scripts/build_preview_template.py` builds it and pins it as a client's
runtime template. The contract lives in
[Preview runtimes and seeding](../../../docs/arch-components/preview-runtime-and-seeding.md#template-and-readiness).

## Building

The build runs on a Mac with Apple's `container` CLI, which builds the fixture's
`deploy/vercel/Dockerfile` for `linux/amd64`:

```sh
set -a; source .env.local; set +a
uv run --project apps/api python apps/api/scripts/build_preview_template.py \
  --client-id <uuid> --configuration-release-id <uuid> --warm-pool-target 1
```

Without `--client-id` it builds, snapshots, and verifies the template and prints
its identity without pinning a template.

The script:

1. Exports the fixture checkout at the recipe's pinned `fixture_revision` with
   `git archive`, under `~/.cache/utrace/preview-template`, because the
   `container` builder reads only the home directory.
2. Builds the image with the recipe's build arguments and pushes it to
   `<UTRACE_PREVIEW_IMAGE_NAMESPACE>/<image_repository>`, tagged with the recipe
   digest prefix, logging in to `vcr.vercel.com` as `oidc` with the grant's token.
3. Takes the template snapshot, with no expiry, from a fresh sandbox of the image
   pinned by digest.
4. Creates one runtime from the snapshot, starts the supervisor, waits for
   `GET /api/health`, and deletes the runtime.
5. With `--client-id`, records the image reference, snapshot ID, recipe digest,
   seed identity, and SDK versions as the client's current template.

## Environment

| Variable | Meaning |
| --- | --- |
| `UTRACE_PREVIEW_TEMPLATE_RECIPE` | Absolute path to `shopalytics-recipe.json`. |
| `UTRACE_PREVIEW_FIXTURE_REPOSITORY` | The Shopalytics checkout to stage the build context from. |
| `UTRACE_PREVIEW_IMAGE_NAMESPACE` | `vcr.vercel.com/<team slug>/<project name>`. |
| `UTRACE_VERCEL_SANDBOX_TOKEN`, `UTRACE_VERCEL_TEAM_ID`, `UTRACE_VERCEL_PROJECT_ID` | The sandbox grant. Locally, a project OIDC token from `vercel env pull`. |
| The `UTRACE_PREVIEW_*` policy set `PreviewSettings` declares | The verification runtime runs under the same policy as any other preview runtime. |

## Runtime behavior

Vercel Sandbox does not run an image's `ENTRYPOINT` or `CMD`. The control plane
starts `supervisor_command` as a detached command as the image's default user,
which is root in the Postgres base image, and restarts the product by stopping
that supervisor and starting it again. A runtime is created with
`SHOPALYTICS_SEED_TARGET`, `UTRACE_PROFILE=preview`, `UTRACE_PREVIEW_HANDOFF_SECRET`,
and `UTRACE_PREVIEW_ROUTE_SIGNING_SECRET`; the Server SDK stays dormant until the
claim writes `/srv/shopalytics/.utrace-activation` and restarts the supervisor.

The runtime's egress allows only the Server SDK's control and ingestion hosts; a
candidate additionally reaches the npm registry to build the approved patch. Both
policies live in `apps/api/app/preview/template.py`.

## Readiness probes

`gating_probes` must pass, or be declared absent in `not_applicable_probes`,
before a route may be committed. `browser_observation` and `trace_correlation`
are advisory at route commit because they need the participant's own traffic;
`confirm_session_observation` in `apps/api/app/preview/recovery.py` answers them
once the session is live.
