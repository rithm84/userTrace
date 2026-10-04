"""The digest has to be reproducible across both bindings and the standard.

``fixtures/_digest/vectors.json`` carries hand-written RFC 8785 canonical forms
and their SHA-256 digests. The TypeScript suite reads the same file, so passing
here and there is the cross-language proof.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

import pytest

from tests.conftest import FIXTURES
from utrace_contracts import FIXTURES_ROOT, payload_digest
from utrace_contracts.base import JsonValue
from utrace_contracts.canonical import canonicalize
from utrace_contracts.envelope import validate_event
from utrace_contracts.errors import DigestMismatchError

_VECTORS_PATH = FIXTURES_ROOT / "_digest" / "vectors.json"
_VECTOR_DOCUMENT = json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))
_VECTORS: list[Mapping[str, JsonValue]] = _VECTOR_DOCUMENT["vectors"]


def test_vector_file_declares_the_envelope_algorithms() -> None:
    assert _VECTOR_DOCUMENT["algorithm"] == "sha256"
    assert _VECTOR_DOCUMENT["canonicalization"] == "jcs"
    assert len(_VECTORS) >= 8


@pytest.mark.parametrize("vector", _VECTORS, ids=lambda vector: str(vector["name"]))
def test_canonical_form_matches_the_vector(vector: Mapping[str, JsonValue]) -> None:
    assert canonicalize(vector["value"]).decode("utf-8") == vector["canonical"]


@pytest.mark.parametrize("vector", _VECTORS, ids=lambda vector: str(vector["name"]))
def test_digest_matches_the_vector(vector: Mapping[str, JsonValue]) -> None:
    canonical = str(vector["canonical"])
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == vector["digest"]
    assert payload_digest(vector["value"]) == vector["digest"]


def test_valid_envelope_fixtures_carry_an_honest_digest() -> None:
    """A fixture that claims a digest must be one the payload actually produces."""
    envelopes = [
        fixture
        for fixture in FIXTURES
        if fixture.expect_valid
        and fixture.area == "envelope"
        and fixture.contract in {"envelope.v1", "event.v1", "command.v1", "result.v1"}
    ]
    assert envelopes, "expected envelope-family fixtures to check digests against"
    for fixture in envelopes:
        instance = fixture.instance
        assert isinstance(instance, dict)
        assert payload_digest(instance["payload"]) == instance["digest"]["value"], (
            f"{fixture.path} declares a digest its payload does not produce"
        )


def test_verify_digest_rejects_a_tampered_payload() -> None:
    """Changing the payload without the digest is caught, not silently accepted."""
    fixture = next(
        f for f in FIXTURES if f.expect_valid and f.contract == "event.v1" and f.case == "minimal"
    )
    instance = fixture.instance
    assert isinstance(instance, dict)
    validate_event(instance).verify_digest()

    tampered = {**instance, "payload": {**instance["payload"], "summary": "something else"}}
    with pytest.raises(DigestMismatchError) as raised:
        validate_event(tampered).verify_digest()
    assert raised.value.declared == instance["digest"]["value"]
    assert raised.value.computed != raised.value.declared
