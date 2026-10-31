"""A long-lived sandbox runs on a short deadline that is extended only when due.

The live test creates a real two-minute sandbox and deletes it in a `finally`.
"""

from datetime import UTC, datetime, timedelta

from app.connectors.vercel_sandboxes import EgressPolicy, VercelSandboxes
from app.execution.sandbox_deadlines import (
    DEADLINE_SECONDS,
    EXTEND_WITHIN_SECONDS,
    extension_seconds,
    initial_deadline_seconds,
    keep_alive,
)
from app.ids import uuid7

_NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
# Provider timestamps are whole milliseconds and an extension is whole seconds.
_TOLERANCE = timedelta(seconds=30)


def test_a_deadline_is_extended_only_when_due_and_never_past_its_planned_end() -> None:
    far = _NOW + timedelta(hours=2)
    # Not yet due: left alone.
    assert extension_seconds(deadline=_NOW + timedelta(seconds=EXTEND_WITHIN_SECONDS), planned_end=far, now=_NOW) == 0
    # Due: extended to the full short deadline from now.
    assert extension_seconds(deadline=_NOW + timedelta(minutes=5), planned_end=far, now=_NOW) == DEADLINE_SECONDS - 300
    # Due, but the planned end is nearer: extended only to it.
    assert extension_seconds(
        deadline=_NOW + timedelta(minutes=5), planned_end=_NOW + timedelta(minutes=8), now=_NOW
    ) == 180
    # Already at or past its planned end: never extended.
    assert extension_seconds(deadline=_NOW + timedelta(minutes=5), planned_end=_NOW, now=_NOW) == 0
    # A sandbox is created with the shorter of its planned life and the short deadline.
    assert initial_deadline_seconds(3600) == DEADLINE_SECONDS
    assert initial_deadline_seconds(300) == 300


async def _deadline(sandboxes: VercelSandboxes, name: str) -> datetime:
    handle = await sandboxes.find(name=name)
    assert handle is not None
    deadline = sandboxes.running_deadline(handle)
    assert deadline is not None
    return deadline


async def test_a_real_sandbox_deadline_moves_toward_its_planned_end(sandboxes: VercelSandboxes) -> None:
    name = f"utrace-deadline-test-{uuid7().hex}"
    await sandboxes.create_from_default_image(
        name=name,
        env={},
        egress=EgressPolicy(allowed_domains=(), injections=()),
        execution_time_limit_seconds=120,
        vcpus=1,
    )
    try:
        created = datetime.now(tz=UTC)
        assert abs(await _deadline(sandboxes, name) - (created + timedelta(seconds=120))) < _TOLERANCE

        # Due, with a near planned end: extended only to that end.
        near_end = datetime.now(tz=UTC) + timedelta(seconds=300)
        handle = await sandboxes.attach(name=name)
        await keep_alive(sandboxes, handle, planned_end=near_end)
        assert abs(await _deadline(sandboxes, name) - near_end) < _TOLERANCE

        # Still due, with a far planned end: extended to the short deadline from now.
        handle = await sandboxes.attach(name=name)
        await keep_alive(sandboxes, handle, planned_end=datetime.now(tz=UTC) + timedelta(hours=2))
        extended = await _deadline(sandboxes, name)
        assert abs(extended - (datetime.now(tz=UTC) + timedelta(seconds=DEADLINE_SECONDS))) < _TOLERANCE

        # No longer due: left alone.
        handle = await sandboxes.attach(name=name)
        await keep_alive(sandboxes, handle, planned_end=datetime.now(tz=UTC) + timedelta(hours=2))
        assert await _deadline(sandboxes, name) == extended
    finally:
        await sandboxes.delete(await sandboxes.attach(name=name))
