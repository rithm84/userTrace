"""The wire models are held to the shared JSON Schemas and their fixtures.

`packages/utrace-contracts` publishes no Python bindings for the
`browser-observation` area yet, so `app.ingestion.frames` mirrors those schemas
by hand. These tests check the mirror from both directions: the canonical
fixtures a binding must accept, and the invalid fixtures it must reject, each
validated against the JSON Schema as well as against the pydantic model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.ingestion.errors import SourceValidationError
from app.ingestion.frames import ObservationEnvelope, decode_client_frame

CONTRACTS = Path(__file__).resolve().parents[4] / "packages" / "utrace-contracts"
SCHEMAS = CONTRACTS / "schemas"
FIXTURES = CONTRACTS / "fixtures" / "browser-observation"


def _registry() -> Registry[object]:
    """Load every contract schema so `$ref`s across areas resolve offline."""
    registry: Registry[object] = Registry()
    for path in SCHEMAS.rglob("*.schema.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(
            uri=str(document["$id"]), resource=Resource.from_contents(document)
        )
    return registry


def _validator(area: str, name: str) -> Draft202012Validator:
    """Return a validator for one published schema document."""
    document = json.loads(
        (SCHEMAS / area / f"{name}.v1.schema.json").read_text(encoding="utf-8")
    )
    return Draft202012Validator(document, registry=_registry())


def _document_of(name: str) -> str:
    """Return the schema document one fixture file name selects."""
    return name.split(".", maxsplit=1)[0]


def _fixture_paths(directory: str, document: str) -> list[Path]:
    """Return every shared fixture that exercises one schema document.

    Fixtures are found by the document their name selects rather than by an
    exact file name, so a fixture rename in the contracts package cannot make
    these tests silently stop exercising a document.
    """
    found = sorted(
        path
        for path in (FIXTURES / directory).glob("*.json")
        if _document_of(path.name) == document
    )
    if len(found) == 0:
        raise AssertionError(f"no {directory} fixture exercises {document}")
    return found


def _read(path: Path) -> dict[str, object]:
    """Read one shared fixture."""
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise TypeError(f"fixture {path.name} is not an object")
    return decoded


def test_the_client_frame_model_accepts_every_canonical_frame_fixture() -> None:
    """Each shared valid fixture passes both the JSON Schema and the model."""
    validator = _validator("browser-observation", "ingest-client-frame")
    for path in _fixture_paths("valid", "ingest-client-frame"):
        fixture = _read(path)
        validator.validate(fixture)
        frame = decode_client_frame(json.dumps(fixture))
        assert frame.kind == fixture["kind"]


def test_the_envelope_model_accepts_every_canonical_observation_fixture() -> None:
    """Every published observation-envelope fixture decodes into the model."""
    validator = _validator("browser-observation", "observation-envelope")
    for path in _fixture_paths("valid", "observation-envelope"):
        fixture = _read(path)
        validator.validate(fixture)
        envelope = ObservationEnvelope.model_validate_json(
            json.dumps(fixture), strict=True
        )
        assert str(envelope.observation_id) == fixture["observation_id"]


@pytest.mark.parametrize(
    "name",
    sorted(path.name for path in (FIXTURES / "invalid").glob("*.json")),
)
def test_the_models_reject_every_invalid_fixture(name: str) -> None:
    """An instance the schema rejects is an instance the model rejects."""
    fixture = _read(FIXTURES / "invalid" / name)
    document = _document_of(name)
    validator = _validator("browser-observation", document)
    assert not validator.is_valid(fixture), f"{name} unexpectedly satisfies its schema"
    if document == "ingest-client-frame":
        with pytest.raises(SourceValidationError):
            decode_client_frame(json.dumps(fixture))
    elif document == "observation-envelope":
        with pytest.raises(ValueError):
            ObservationEnvelope.model_validate_json(json.dumps(fixture), strict=True)


def _batch_fixture() -> dict[str, object]:
    """Return the canonical observation batch fixture."""
    for path in _fixture_paths("valid", "ingest-client-frame"):
        fixture = _read(path)
        if fixture["kind"] == "observation.batch":
            return fixture
    raise AssertionError("no valid fixture carries an observation.batch frame")


def test_a_string_never_becomes_an_integer_sequence() -> None:
    """Strict JSON validation keeps a quoted sequence from passing as a number."""
    fixture = _batch_fixture()
    fixture["first_export_sequence"] = str(fixture["first_export_sequence"])
    with pytest.raises(SourceValidationError):
        decode_client_frame(json.dumps(fixture))


def test_an_unknown_field_fails_closed() -> None:
    """Unknown record types, versions, attributes, and fields fail validation."""
    fixture = _batch_fixture()
    fixture["unexpected"] = True
    with pytest.raises(SourceValidationError):
        decode_client_frame(json.dumps(fixture))
