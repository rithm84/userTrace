"""When a browser observation happened, on the server's clock.

The browser stamps each observation twice: with its own wall clock (`occurred_at`),
which can be minutes off on a participant's machine, and with the page's monotonic
clock (`monotonic_offset`, milliseconds since that page's time origin), which no
clock change moves. A page here is one browser stream and frame, since each frame
keeps its own time origin.

Every observation reaches the server after it happened. So for each page, the
earliest acceptance of anything it sent, minus the monotonic offset that record
carried, places the page's time origin on the server's clock, and an observation's
server time is that origin plus its own monotonic offset. It is never earlier than
the moment the observation happened, and later only by the page's fastest delivery,
a fraction of a second. Records the browser buffered and sent late only widen their
own bound, so they never move it. Acceptance is stamped by the database, so this is
the same clock every other server time comes from.

Every comparison of browser activity with a server time (a validation window, a
query bound, a frozen evidence cutoff) reads `observed_at`, never the browser's
wall clock.

Contract: docs/arch-components/telemetry-and-evidence.md
"""

from typing import Final

__all__ = ["SESSION_OBSERVATIONS"]

#: A common table expression, `session_observations`, of one session's browser
#: observations with their server time (`observed_at`) and the preview each came
#: from. It reads the named parameters `client` and `session`.
SESSION_OBSERVATIONS: Final[str] = """
    session_observations as (
        select observations.*,
               activations.preview_runtime_allocation_id,
               observations.monotonic_offset * interval '1 millisecond'
               + min(
                   observations.accepted_at
                   - observations.monotonic_offset * interval '1 millisecond'
                 ) over (
                   partition by observations.browser_stream_id, observations.frame_id
                 ) as observed_at
        from public.browser_observations as observations
        join public.telemetry_activations as activations
          on activations.id = observations.activation_id
        where observations.client_id = %(client)s
          and activations.interview_session_id = %(session)s
    )
"""
