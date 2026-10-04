"""The control socket, proven with the real ``@utrace/server-sdk``.

One Express service runs under the published SDK preload against the running
control plane: it bootstraps, activates, exports real spans over OTLP, proves its
propagation probe, drives readiness to ``ready``, rotates, survives a control
reconnect, and answers pause, resume, and terminate — both the internal service
route's commands and the ones a participant's own Pause and Resume produce.
Nothing here is simulated, so what passes is the protocol the SDK actually
speaks.

The journey is one test because the service and its activation are one long-lived
subject: the database fixture truncates between tests, so splitting the journey
would mean starting a new process per assertion and proving nothing about the
stream that survives them.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from json import dumps, loads
from uuid import UUID

import httpx
import pytest
import websockets
from websockets.exceptions import WebSocketException

from app.adapters.observation_readiness import IngestionObservationReadiness
from app.db.pool import Database
from app.ids import uuid7
from app.preview.ports import TelemetryReadiness
from app.settings import Settings
from tests.ingestion.control_observations import (
    active_server_activation,
    call_product_route,
    command_acknowledgement,
    drain_ingestion_jobs,
    issue_command,
    latest_health,
    send_browser_half,
    span_count,
    wait_for,
)
from tests.ingestion.factories import PreviewFixture, create_preview
from tests.ingestion.keys import SigningKeys
from tests.ingestion.server_sdk_harness import (
    PROBE_ROUTE_TEMPLATE,
    SERVICE_KEY,
    bootstrap_credential,
    build_sdk,
    full_server_manifest,
    prepare_product_schema,
    publish_server_manifest,
    sign_server_manifest,
    start_service,
    trust_anchor_env_value,
)
from tests.supabase_auth import SignedInUser


async def _bind_participant(
    database: Database, *, preview: PreviewFixture, auth_user_id: UUID
) -> None:
    """Give the session's originating user a real Supabase Auth identity.

    The preview fixture creates the case without one, because most ingestion
    tests never sign in as the participant. The session tab's controls are
    authorized by that identity, so the journey binds it before posting one.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.users set auth_user_id = %s where id = %s",
            (auth_user_id, preview.case.utrace_user_id),
        )


async def _participant_control(
    ingest_server: str, *, access_token: str, session_id: UUID, action: str
) -> None:
    """Post one participant control exactly as the session tab posts it."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{ingest_server}/v1/sessions/{session_id}/controls",
            headers={"authorization": f"Bearer {access_token}"},
            json={"action": action},
        )
    assert response.status_code == 200, response.text


async def _new_acknowledged_command(
    database: Database, *, activation_id: UUID, kind: str, already: set[UUID]
) -> Mapping[str, object] | None:
    """Return an acknowledged control command of one kind the test has not seen."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, reason, acknowledged_lifecycle_state
            from public.server_control_commands
            where activation_id = %s and kind = %s
              and acknowledged_lifecycle_state is not null
            order by issued_at desc
            """,
            (activation_id, kind),
        )
        rows = list(await cur.fetchall())
    for row in rows:
        if UUID(str(row["id"])) not in already:
            return row
    return None


async def test_a_foreign_bootstrap_credential_cannot_open_the_control_socket(
    ingest_server: str,
) -> None:
    """A credential the control plane did not sign never reaches the loop."""
    control_url = f"{ingest_server.replace('http://', 'ws://')}/v1/server-sdk/control"
    with pytest.raises(WebSocketException):
        async with websockets.connect(
            control_url,
            additional_headers={"authorization": "Bearer not-a-lane-credential"},
            open_timeout=15,
        ):
            pytest.fail("the control socket accepted an unsigned credential")


async def test_the_real_server_sdk_activates_exports_probes_and_obeys_control(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    ingest_websocket_url: str,
    auth_users: dict[str, SignedInUser],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """One prepared service's whole control-channel journey, end to end."""
    preload = build_sdk()
    await prepare_product_schema(telemetry_database)
    preview = await create_preview(
        telemetry_database,
        name=f"sdk-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    await publish_server_manifest(
        telemetry_database,
        preview=preview,
        signed=sign_server_manifest(
            full_server_manifest(client_id=preview.case.client_id), keys
        ),
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    bootstrap_token = bootstrap_credential(settings, preview)
    process = start_service(
        preload=preload,
        control_origin=ingest_server.replace("http://", "ws://"),
        ingest_origin=ingest_server,
        bootstrap_token=bootstrap_token,
        trust_anchor=trust_anchor_env_value(keys),
        database_url=settings.database_url,
        scratch=tmp_path_factory.mktemp("server-sdk"),
    )
    try:
        # Bootstrap and activate: the identities come from the committed
        # allocation, never from the process's own claims.
        activation = await wait_for(
            "the Server SDK to activate",
            lambda: active_server_activation(telemetry_database, preview=preview),
            process=process,
        )
        activation_id = UUID(str(activation["id"]))
        server_stream_id = UUID(str(activation["server_stream_id"]))
        entry_token = str(activation["preview_entry_token"])
        assert activation["service_key"] == SERVICE_KEY
        assert activation["interview_session_id"] == preview.interview_session_id
        assert (
            activation["preview_environment_id"] == preview.preview_environment_id
        )
        # The probe's hops follow from the manifest's required adapters:
        # node_http and express produce the server span, pg the database hop.
        assert sorted(str(hop) for hop in activation["required_hops"]) == [
            "database",
            "server",
        ]
        assert len(entry_token) >= 16

        # Export: the browser half first, then the product request that carries
        # the same trace context through the authenticated preview gate.
        trace_id = secrets.token_hex(16)
        client_span_id = secrets.token_hex(8)
        await send_browser_half(
            ingest_server=ingest_server,
            ingest_websocket_url=ingest_websocket_url,
            settings=settings,
            keys=keys,
            preview=preview,
            trace_id=trace_id,
            client_span_id=client_span_id,
        )
        assert (
            await call_product_route(
                trace_id=trace_id,
                client_span_id=client_span_id,
                entry_token=entry_token,
            )
            == 200
        )

        async def exported_spans() -> list[Mapping[str, object]] | None:
            async with telemetry_database.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    select span_name, span_kind, parent_span_id, attributes,
                           scope_name
                    from public.server_spans
                    where client_id = %s and trace_id = %s
                    """,
                    (preview.case.client_id, trace_id),
                )
                rows = list(await cur.fetchall())
            kinds = {str(row["span_kind"]) for row in rows}
            return rows if {"server", "client"} <= kinds else None

        rows = await wait_for(
            "the SERVER span and its database child", exported_spans, process=process
        )
        server = next(row for row in rows if row["span_kind"] == "server")
        database_span = next(row for row in rows if row["span_kind"] == "client")
        # The span name is the manifest route template, never the raw URL, and
        # the server span names the browser CLIENT span as its remote parent.
        assert server["span_name"] == f"GET {PROBE_ROUTE_TEMPLATE}"
        assert server["parent_span_id"] == client_span_id
        assert database_span["scope_name"] == "@opentelemetry/instrumentation-pg"
        attributes = database_span["attributes"]
        assert isinstance(attributes, dict)
        assert attributes["db.system.name"] == "postgresql"
        # Redaction is the SDK's own: the SQL text never leaves the process.
        assert "db.query.text" not in attributes
        assert "db.statement" not in attributes

        # Probe and readiness: the probe passes only once its batch is
        # acknowledged, and readiness follows from the projections it drives.
        async def probe_passed() -> Mapping[str, object] | None:
            row = await latest_health(
                telemetry_database, activation_id=activation_id
            )
            if row is None:
                return None
            result = row["probe_result"]
            if not isinstance(result, dict) or result.get("state") != "passed":
                return None
            return row

        health = await wait_for(
            "the propagation probe to pass", probe_passed, process=process
        )
        assert health["lifecycle_state"] == "ready"
        probe = health["probe_result"]
        assert isinstance(probe, dict)
        assert sorted(str(hop) for hop in probe["observed_hops"]) == [
            "database",
            "server",
        ]

        # Readiness is a projection per kind over the whole Interview Session,
        # so it is read the way the preview stream reads it: through the port.
        port = IngestionObservationReadiness(telemetry_database)

        async def all_ready() -> TelemetryReadiness | None:
            await drain_ingestion_jobs(telemetry_database)
            readiness = await port.readiness(
                client_id=preview.case.client_id,
                interview_session_id=preview.interview_session_id,
            )
            if not (
                readiness.browser_ready
                and readiness.server_ready
                and readiness.correlation_ready
            ):
                return None
            return readiness

        readiness = await wait_for(
            "browser, server, and correlation readiness", all_ready, process=process
        )
        assert readiness.detail == ""

        # The same port answers the preview stream's activation probe.
        observed = await port.activation(
            client_id=preview.case.client_id,
            interview_session_id=preview.interview_session_id,
            preview_runtime_allocation_id=preview.preview_runtime_allocation_id,
        )
        assert observed.activated
        assert observed.service_name == SERVICE_KEY
        assert observed.preview_entry_token == entry_token
        assert observed.sdk_version != "unreported"

        # Rotation and reconnect: a second real control socket presenting the
        # same bootstrap credential rotates against the same stream, which is
        # exactly what the SDK does after a dropped control connection.
        control_url = (
            f"{ingest_server.replace('http://', 'ws://')}/v1/server-sdk/control"
        )
        async with websockets.connect(
            control_url,
            additional_headers={"authorization": f"Bearer {bootstrap_token}"},
            open_timeout=15,
        ) as reconnected:
            await reconnected.send(
                dumps(
                    {
                        "type": "rotate",
                        "request_id": str(uuid7()),
                        "server_stream_id": str(server_stream_id),
                    }
                )
            )
            reply = loads(await asyncio.wait_for(reconnected.recv(), timeout=20))
            assert reply["type"] == "rotated"
            assert len(str(reply["credential"]["token"])) > 0

            # A stream this credential's allocation does not own is refused.
            await reconnected.send(
                dumps(
                    {
                        "type": "rotate",
                        "request_id": str(uuid7()),
                        "server_stream_id": str(uuid7()),
                    }
                )
            )
            refusal = loads(await asyncio.wait_for(reconnected.recv(), timeout=20))
            assert refusal["type"] == "error"
            assert refusal["failure"]["code"] == "invalid_activation"

        async with telemetry_database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select count(*) as streams, count(distinct activation_id) as bindings
                from public.server_streams where activation_id = %s
                """,
                (activation_id,),
            )
            streams = await cur.fetchone()
        assert streams is not None
        assert int(str(streams["streams"])) == 1
        assert int(str(streams["bindings"])) == 1

        # The SDK's own socket is untouched: a later health report still arrives.
        before_report = health["reported_at"]

        async def newer_report() -> Mapping[str, object] | None:
            row = await latest_health(
                telemetry_database, activation_id=activation_id
            )
            if row is None or row["reported_at"] == before_report:
                return None
            return row

        assert await wait_for(
            "a health report after the reconnect", newer_report, process=process
        )

        # Pause stops export, and resume restores it.
        pause_id = await issue_command(
            ingest_server,
            service_token=settings.front_door_service_token,
            activation_id=activation_id,
            kind="pause",
            reason="session_paused",
        )
        assert (
            await wait_for(
                "the pause acknowledgement",
                lambda: command_acknowledgement(
                    telemetry_database, command_id=pause_id
                ),
                process=process,
            )
            == "paused"
        )

        paused_trace = secrets.token_hex(16)
        assert (
            await call_product_route(
                trace_id=paused_trace,
                client_span_id=secrets.token_hex(8),
                entry_token=entry_token,
            )
            == 200
        )
        await asyncio.sleep(5)
        assert await span_count(telemetry_database, trace_id=paused_trace) == 0

        resume_id = await issue_command(
            ingest_server,
            service_token=settings.front_door_service_token,
            activation_id=activation_id,
            kind="resume",
            reason="session_resumed",
        )
        assert await wait_for(
            "the resume acknowledgement",
            lambda: command_acknowledgement(
                telemetry_database, command_id=resume_id
            ),
            process=process,
        ) in {"activating", "ready", "recovering"}

        resumed_trace = secrets.token_hex(16)
        assert (
            await call_product_route(
                trace_id=resumed_trace,
                client_span_id=secrets.token_hex(8),
                entry_token=entry_token,
            )
            == 200
        )

        async def resumed_spans() -> bool | None:
            return (
                True
                if await span_count(telemetry_database, trace_id=resumed_trace) > 0
                else None
            )

        assert await wait_for(
            "export to resume", resumed_spans, process=process
        )

        # The participant's own Pause reaches the same SDK. The session tab
        # posts the control, the control plane commands every server stream it
        # holds for that Interview Session through the control hub, and the real
        # SDK answers. The service route above is the internal path; this is the
        # one a person drives.
        await _bind_participant(
            telemetry_database,
            preview=preview,
            auth_user_id=auth_users["participant"].auth_user_id,
        )
        participant_token = auth_users["participant"].access_token
        await _participant_control(
            ingest_server,
            access_token=participant_token,
            session_id=preview.interview_session_id,
            action="pause",
        )
        participant_pause = await wait_for(
            "the participant's pause to reach the Server SDK",
            lambda: _new_acknowledged_command(
                telemetry_database,
                activation_id=activation_id,
                kind="pause",
                already={pause_id},
            ),
            process=process,
        )
        assert participant_pause["reason"] == "session_paused"
        assert participant_pause["acknowledged_lifecycle_state"] == "paused"

        await _participant_control(
            ingest_server,
            access_token=participant_token,
            session_id=preview.interview_session_id,
            action="resume",
        )
        participant_resume = await wait_for(
            "the participant's resume to reach the Server SDK",
            lambda: _new_acknowledged_command(
                telemetry_database,
                activation_id=activation_id,
                kind="resume",
                already={resume_id},
            ),
            process=process,
        )
        assert participant_resume["reason"] == "session_resumed"
        assert participant_resume["acknowledged_lifecycle_state"] in {
            "activating",
            "ready",
            "recovering",
        }

        # Terminate ends the stream with its terminal report.
        terminate_id = await issue_command(
            ingest_server,
            service_token=settings.front_door_service_token,
            activation_id=activation_id,
            kind="terminate",
            reason="session_ended",
        )
        assert (
            await wait_for(
                "the terminate acknowledgement",
                lambda: command_acknowledgement(
                    telemetry_database, command_id=terminate_id
                ),
                process=process,
            )
            is not None
        )

        async def stream_closed() -> str | None:
            async with telemetry_database.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "select state from public.server_streams where id = %s",
                    (server_stream_id,),
                )
                row = await cur.fetchone()
            if row is None or str(row["state"]) == "open":
                return None
            return str(row["state"])

        assert (
            await wait_for(
                "the terminal stream report", stream_closed, process=process
            )
            == "ended"
        )
    finally:
        process.stop()


async def test_a_closed_interview_session_revokes_the_bootstrap_credential(
    telemetry_database: Database,
    settings: Settings,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """Ending the session stops new control connections without waiting for expiry."""
    preview = await create_preview(
        telemetry_database,
        name=f"revoked-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    token = bootstrap_credential(settings, preview)
    control_url = f"{ingest_server.replace('http://', 'ws://')}/v1/server-sdk/control"

    # While the session is live the same credential opens the socket.
    async with websockets.connect(
        control_url,
        additional_headers={"authorization": f"Bearer {token}"},
        open_timeout=15,
    ) as opened:
        assert opened.state.name == "OPEN"

    async with telemetry_database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.interview_sessions set state = 'closed' where id = %s",
            (preview.interview_session_id,),
        )

    with pytest.raises(WebSocketException):
        async with websockets.connect(
            control_url,
            additional_headers={"authorization": f"Bearer {token}"},
            open_timeout=15,
        ):
            pytest.fail("a closed Interview Session still accepted its credential")
