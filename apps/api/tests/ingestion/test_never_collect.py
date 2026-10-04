"""The payment-card exclusion: card numbers are refused, opaque identifiers are not."""

import pytest

from app.ingestion.errors import NeverCollectViolationError
from app.ingestion.never_collect import assert_no_forbidden_material


def test_a_standalone_card_number_is_refused() -> None:
    with pytest.raises(NeverCollectViolationError) as refused:
        assert_no_forbidden_material({"label": "card 4111 1111 1111 1111"}, "payload", 0)
    assert refused.value.rule_id == "platform.payment_card"


def test_a_luhn_valid_run_inside_an_opaque_identifier_is_accepted() -> None:
    """A browser SDK target id is random hex, so it can hold a Luhn-valid digit run."""
    assert_no_forbidden_material(
        {"target": {"target_id": "tgt_179_5d4222222222222d"}}, "payload", 0
    )
