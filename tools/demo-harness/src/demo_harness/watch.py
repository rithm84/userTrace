"""``demo watch``: follow the live journey and print each milestone as it commits.

Milestones are read from the control plane's durable projections, never from a
provider or a model's narration. A state ``docs/SCENARIO.md`` forbids stops the
watch immediately with a non-zero status, because a demo that has already left
the happy path should not be allowed to look like it is still on it.

Watching accumulates: a milestone that has committed stays committed even once
its record moves on, which is how a live Interview Session and an allocated
preview runtime are still counted after cleanup has taken them back. The journey
is complete only when every milestone has been announced, so this must run
across the whole demo rather than be started at the end.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID

from demo_harness.checks import Finding, failed
from demo_harness.control_plane import ControlPlane
from demo_harness.journey import MILESTONES, NoScenarioCaseError, observe, prohibitions, reached

__all__ = ["watch"]


def _stamp() -> str:
    """Return the current instant, to the second, in UTC."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


async def watch(
    control_plane: ControlPlane,
    *,
    client_id: UUID,
    poll_seconds: float,
    deadline_seconds: float,
) -> int:
    """Follow the journey until it completes, breaks, or the deadline passes."""
    sys.stdout.write(f"{_stamp()}  watching {control_plane.base_url} for client {client_id}\n")

    announced: set[str] = set()
    started = asyncio.get_running_loop().time()
    order = [milestone.name for milestone in MILESTONES]

    while True:
        try:
            snapshot = await observe(control_plane, client_id=client_id)
        except NoScenarioCaseError as waiting:
            sys.stdout.write(f"{_stamp()}  waiting: {waiting}\n")
            if not await _sleep_within(started, poll_seconds, deadline_seconds):
                return _timed_out(announced)
            continue

        broken = failed(prohibitions(snapshot))
        if broken:
            return _stop_on_violation(broken)

        committed = reached(snapshot)
        for name in order:
            if name in committed and name not in announced:
                announced.add(name)
                sys.stdout.write(f"{_stamp()}  {name}: {committed[name]}\n")

        if len(announced) == len(order):
            sys.stdout.write(
                f"{_stamp()}  journey complete: {len(announced)} milestones committed\n"
            )
            return 0

        if not await _sleep_within(started, poll_seconds, deadline_seconds):
            return _timed_out(announced)


async def _sleep_within(started: float, poll_seconds: float, deadline_seconds: float) -> bool:
    """Sleep one poll interval, or report that the deadline has passed."""
    elapsed = asyncio.get_running_loop().time() - started
    if elapsed + poll_seconds > deadline_seconds:
        return False
    await asyncio.sleep(poll_seconds)
    return True


def _timed_out(announced: set[str]) -> int:
    """Report the milestones reached before the deadline and fail."""
    pending = [milestone.name for milestone in MILESTONES if milestone.name not in announced]
    sys.stdout.write(
        f"{_stamp()}  deadline reached with {len(announced)} milestones committed; "
        f"still pending: {pending}\n"
    )
    return 1


def _stop_on_violation(broken: list[Finding]) -> int:
    """Report every forbidden state and fail immediately."""
    sys.stdout.write(f"{_stamp()}  the journey left the happy path:\n")
    for finding in broken:
        sys.stdout.write(f"  [FAIL] {finding.name}: {finding.detail}\n")
    return 1
