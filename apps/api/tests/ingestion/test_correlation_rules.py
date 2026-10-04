"""The pure correlation rules: what counts as a join, and what orders a timeline."""

from __future__ import annotations

import secrets

from app.ingestion.correlation import (
    CorrelationInput,
    ServerSpanRef,
    evaluate_correlation,
    ordering_key,
)


def test_seeing_both_halves_separately_is_not_correlation() -> None:
    """Without the exact parentage the trace stays partial, never complete."""
    browser_span = secrets.token_hex(8)
    unrelated = evaluate_correlation(
        CorrelationInput(
            trace_id=secrets.token_hex(16),
            browser_client_span_ids=frozenset({browser_span}),
            browser_request_ids=frozenset({"req_1"}),
            server_spans=(
                ServerSpanRef(
                    span_id=secrets.token_hex(8),
                    parent_span_id=None,
                    span_kind="server",
                    link_span_ids=(),
                    start_time_unix_nano=1,
                ),
            ),
            required_downstream_hops=frozenset({"server"}),
        )
    )
    assert unrelated.status == "partial"
    assert "browser_to_server_parentage" in unrelated.gaps


def test_parallel_requests_in_one_trace_correlate_and_a_shared_parent_is_ambiguous() -> None:
    """One interaction's concurrent requests each join their own browser span.

    Two server spans naming the same browser parent remain ambiguous.
    """
    first, second = secrets.token_hex(8), secrets.token_hex(8)

    def server_span(parent: str) -> ServerSpanRef:
        return ServerSpanRef(
            span_id=secrets.token_hex(8),
            parent_span_id=parent,
            span_kind="server",
            link_span_ids=(),
            start_time_unix_nano=1,
        )

    def evaluate(*parents: str) -> str:
        return evaluate_correlation(
            CorrelationInput(
                trace_id=secrets.token_hex(16),
                browser_client_span_ids=frozenset({first, second}),
                browser_request_ids=frozenset({"req_1", "req_2"}),
                server_spans=tuple(server_span(parent) for parent in parents),
                required_downstream_hops=frozenset({"server"}),
            )
        ).status

    assert evaluate(first, second) == "complete"
    assert evaluate(first, first) == "ambiguous"


def test_ordering_authority_prefers_source_sequence_over_clock_time() -> None:
    """A record with a source sequence sorts ahead of one with only a clock."""
    sequenced = ordering_key(
        source_sequence=2,
        parent_depth=None,
        paired_request_id=None,
        clock_adjusted_nanos=9_000,
    )
    clock_only = ordering_key(
        source_sequence=None,
        parent_depth=None,
        paired_request_id=None,
        clock_adjusted_nanos=1_000,
    )
    assert sequenced < clock_only
