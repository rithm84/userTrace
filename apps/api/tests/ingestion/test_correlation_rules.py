"""The pure correlation rules: what counts as a join."""

from __future__ import annotations

import secrets

from app.ingestion.correlation import (
    CorrelationInput,
    ServerSpanRef,
    evaluate_correlation,
)


def test_seeing_both_halves_separately_is_not_correlation() -> None:
    """Without the exact parentage the trace stays partial, never complete."""
    browser_span = secrets.token_hex(8)
    unrelated = evaluate_correlation(
        CorrelationInput(
            browser_client_span_ids=frozenset({browser_span}),
            server_spans=(
                ServerSpanRef(
                    span_id=secrets.token_hex(8),
                    parent_span_id=None,
                    span_kind="server",
                    link_span_ids=(),
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
        )

    def evaluate(*parents: str) -> str:
        return evaluate_correlation(
            CorrelationInput(
                browser_client_span_ids=frozenset({first, second}),
                server_spans=tuple(server_span(parent) for parent in parents),
                required_downstream_hops=frozenset({"server"}),
            )
        ).status

    assert evaluate(first, second) == "complete"
    assert evaluate(first, first) == "ambiguous"

