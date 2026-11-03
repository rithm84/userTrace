"""Copy one recorded Interview Session out of a database, read-only, as JSON.

    uv run python -m tests.evals.jev.export_session \
        --env-file ../../.env.local --session <interview session id> --out session.json

The database URL is read from `UTRACE_DATABASE_URL` in the named env file, and the
connection is forced read-only before any query runs: a run against production
cannot write. The export holds what the replay set is rebuilt from: transcript
fragments, the participant's preview activity (milestones, clicks, page loads) with
when it was observed and committed, the controls they clicked, every Jev judgement
with the context bundle it read, and the session's agreements and validation
records.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.evidence.observed_time import SESSION_OBSERVATIONS

_PERSON_ACTIVITY = ("product.milestone", "interaction.activation", "baseline.snapshot")


def _database_url(env_file: Path) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^UTRACE_DATABASE_URL=(.*)$", line.strip())
        if match:
            return match.group(1).strip().strip('"').strip("'")
    raise SystemExit(f"UTRACE_DATABASE_URL is not set in {env_file}")


def export(conn: psycopg.Connection[dict[str, Any]], session_id: str) -> dict[str, list[dict[str, Any]]]:
    """Every record the replay set needs for one session."""
    with conn.cursor() as cur:
        cur.execute(
            "select client_id from public.interview_sessions where id = %s", (session_id,)
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no Interview Session {session_id}")
    params = {"session": session_id, "client": row["client_id"], "types": list(_PERSON_ACTIVITY)}
    queries = {
        "session": "select * from public.interview_sessions where id = %(session)s",
        "fragments": """
            select speaker, provider_event_type, start_ms, end_ms, fragment_text, recorded_at,
                   voice_connection_id
            from public.voice_transcript_fragments
            where interview_session_id = %(session)s
            order by recorded_at, start_ms
        """,
        "activity": "with "
        + SESSION_OBSERVATIONS
        + """
            select id, observation_type, observed_at, accepted_at, target_id,
                   document_attachment_id, payload
            from session_observations
            where observation_type = any(%(types)s)
            order by observed_at, id
        """,
        # The newest registry entry of every control the participant clicked, by page.
        "clicked_controls": """
            select distinct on (o.target_id, o.document_attachment_id)
                   o.target_id, o.document_attachment_id, o.payload->'target' as target
            from public.browser_observations o
            join public.telemetry_activations a on a.id = o.activation_id
            where a.interview_session_id = %(session)s
              and o.observation_type = 'target.registry_change'
              and o.target_id in (
                select o2.target_id from public.browser_observations o2
                join public.telemetry_activations a2 on a2.id = o2.activation_id
                where a2.interview_session_id = %(session)s
                  and o2.observation_type = 'interaction.activation')
            order by o.target_id, o.document_attachment_id, o.export_sequence desc
        """,
        "judgements": """
            select * from public.semantic_judgements
            where interview_session_id = %(session)s order by created_at
        """,
        "bundles": """
            select b.id, b.purpose, b.context_revision, b.cutoff_at, b.digest, b.created_at,
                   coalesce(json_agg(json_build_object(
                     'ordinal', i.ordinal, 'section', i.section, 'source_kind', i.source_kind,
                     'source_id', i.source_id, 'source_revision', i.source_revision,
                     'included', i.included, 'omission_reason', i.omission_reason,
                     'body', i.body) order by i.ordinal)
                     filter (where i.id is not null), '[]') as items
            from public.context_bundles b
            left join public.context_bundle_items i on i.context_bundle_id = b.id
            where b.id in (select context_bundle_id from public.semantic_judgements
                           where interview_session_id = %(session)s)
            group by b.id
        """,
        "agreements": """
            select * from public.acceptance_criteria_agreements
            where interview_session_id = %(session)s order by recorded_at
        """,
        "criteria": """
            select * from public.validation_criteria
            where product_signal_id = (select product_signal_id from public.interview_sessions
                                       where id = %(session)s)
            order by criteria_revision, ordinal
        """,
        "validation_attempts": """
            select * from public.validation_attempts
            where interview_session_id = %(session)s order by created_at
        """,
        "validation_outcomes": """
            select * from public.validation_outcomes where validation_attempt_id in
              (select id from public.validation_attempts where interview_session_id = %(session)s)
        """,
    }
    exported: dict[str, list[dict[str, Any]]] = {}
    with conn.cursor() as cur:
        for name, sql in queries.items():
            cur.execute(sql, params)
            exported[name] = cur.fetchall()
    return exported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with psycopg.connect(
        _database_url(args.env_file),
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
        row_factory=dict_row,
        prepare_threshold=None,
    ) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("show transaction_read_only")
            row = cur.fetchone()
            if row is None or row["transaction_read_only"] != "on":
                raise SystemExit("refusing to export: the session is not read-only")
        exported = export(conn, args.session)
    args.out.write_text(json.dumps(exported, default=str, indent=1), encoding="utf-8")
    print({name: len(rows) for name, rows in exported.items()})


if __name__ == "__main__":
    main()
