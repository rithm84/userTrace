# Jev in the live interview

This evaluation asks whether Jev earns its place in the live interview, or whether
the voice agent's own model, GPT-6.1 Sol, which already hears the participant's
preview activity as application notes, makes the same judgments as well. The
measurements are in
[Benchmarks: Jev in the live interview](../../../../../docs/arch-components/benchmarks.md#jev-in-the-live-interview);
the findings and recommendation are in [REPORT.md](./REPORT.md).

Nothing here changes product behavior. The live ablation's "off" arms patch the
running agent from the test.

## What runs where

| Piece | Reads | Calls |
| --- | --- | --- |
| `export_session` | One Interview Session, from a database you name, read-only | Nothing |
| `build_replay_set` | An export | Nothing; writes `replay_set.json` |
| `replay` | `replay_set.json` | Jev and Sol, three times per moment by default |
| `counterfactual` | An export | Sol, `--samples` times per arm |
| `test_jev_ablation_live` | The local database, through the test fixtures | A real call: Daily, Deepgram Flux, Sol, ElevenLabs, Jev |
| `score_ablation` | The live runs' JSON | Nothing |

## Run it

From `apps/api`, with the repository's `.env.local` providing
`VERCEL_AI_GATEWAY_API_KEY` and `OPENAI_API_KEY`. Raw results go under the
repository's `tmp/`, which is ignored.

The labeled set, `replay_set.json`, holds a production session's transcript and
activity, so it is not committed: `build_replay_set` writes it from an export of
that session. The export the measurements used, of the 2026-11-01 recorded run, is
kept outside git on the workstation that ran them, because a demo reset deletes
the production rows it came from:

```text
~/Library/Application Support/Claude/side-session-notes/local_e03c28d9-0a17-4eea-a547-a6355552b4f2/jev-eval/session-2026-11-01.json
```

To export a recorded session again while its rows still exist (the connection is
forced read-only before any query):

```bash
uv run python -m tests.evals.jev.export_session --env-file ../../.env.local --session 01a19627-af05-74fc-ae4a-ccd0146b7997 --out ../../tmp/jev-eval/session-2026-11-01.json
```

Rebuild the labeled set from the export, which also writes the scripted moments:

```bash
uv run python -m tests.evals.jev.build_replay_set --export ../../tmp/jev-eval/session-2026-11-01.json
```

Replay every moment through Jev, Sol, and the rules, then score:

```bash
uv run python -m tests.evals.jev.replay --repeats 3 --out ../../tmp/jev-eval/replay.json
```

```bash
uv run python -m tests.evals.jev.replay --score ../../tmp/jev-eval/replay.json
```

Replay the recorded run's last validation turn with and without Jev's checks lines:

```bash
uv run python -m tests.evals.jev.counterfactual --export ../../tmp/jev-eval/session-2026-11-01.json --samples 30 --out ../../tmp/jev-eval/counterfactual.json
```

Hold one live interview per arm (`jev`, `sol`, or `quiet`) against the local
stack. The API tests truncate the database they run against, so run them only
through the shared local-database wrapper in the repository's git directory, never
with production settings. From the repository root:

```bash
"$(git rev-parse --git-common-dir)/utrace-with-local-db.sh" bash -c 'cd apps/api && UTRACE_JEV_ABLATION=sol .venv/bin/pytest tests/evals/jev/test_jev_ablation_live.py -s'
```

```bash
uv run python -m tests.evals.jev.score_ablation ../../tmp/jev-eval/ablation/*.json
```

## Labels

A recorded moment's label says what a careful observer would want: whether the
agent should have said a few words, and which agreed checks the activity showed.
The labels for the recorded run are in `build_replay_set.py`; each scripted moment
carries its own reason in `scenarios.py`. A label a careful reader could set either
way is marked ambiguous, and every score is also reported without it. One checks
label was marked ambiguous after the first replay, because every judge, the rules
included, disagreed with it; `scenarios.py` says which.
