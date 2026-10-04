"""Conformance: the JSON Schemas, the bindings, and the fixtures must all agree.

The JSON Schema is the conformance boundary between the two languages, so a
binding must accept exactly what its schema accepts and reject exactly what its
schema rejects. Any rule a binding enforces but the schema cannot express would
make the Python and TypeScript packages disagree in production, which is why
these tests assert equality rather than "at least as strict".

The suite discovers fixtures and bindings from the filesystem and package
layout. Adding an area means adding files, not editing this test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest
from pydantic import ValidationError

from tests.conftest import FIXTURES, Fixture
from utrace_contracts import SchemaViolationError, schema_paths, validate_against_schema
from utrace_contracts.errors import UnknownContractError
from utrace_contracts.registry import area_bindings

BOUND_AREAS = frozenset(area_bindings())
"""Areas whose bindings this package ships.

The `envelope` and `ids` areas are the base every other area composes with, so
their bindings live here. An area owned by the workstream that builds it keeps
its bindings with its consumer and runs these same fixtures against them in
that package's own suite; what this package proves for such an area is that the
JSON Schema and the fixtures agree.
"""

BOUND_FIXTURES = [fixture for fixture in FIXTURES if fixture.area in BOUND_AREAS]


@pytest.mark.parametrize("fixture", FIXTURES, ids=str)
def test_schema_agrees_with_fixture(fixture: Fixture) -> None:
    """Every fixture is accepted or rejected by its JSON Schema as its bucket says."""
    if fixture.expect_valid:
        validate_against_schema(fixture.area, fixture.contract, fixture.instance)
        return
    with pytest.raises(SchemaViolationError):
        validate_against_schema(fixture.area, fixture.contract, fixture.instance)


@pytest.mark.parametrize("fixture", BOUND_FIXTURES, ids=str)
def test_binding_agrees_with_fixture(fixture: Fixture) -> None:
    """Every fixture is accepted or rejected by its pydantic binding as its bucket says."""
    bindings = area_bindings()
    validator = bindings[fixture.area].get(fixture.contract)
    assert validator is not None, (
        f"{fixture.area} fixtures reference {fixture.contract}, which is not bound in "
        f"utrace_contracts.{fixture.area}.contracts.AREA_CONTRACTS"
    )
    if fixture.expect_valid:
        validator(fixture.instance)
        return
    with pytest.raises((ValidationError, ValueError)):
        validator(fixture.instance)


def test_a_bound_area_binds_every_one_of_its_schemas() -> None:
    """An area whose bindings live here binds all of them, not a convenient subset."""
    bound = {f"{area}/{name}" for area, names in area_bindings().items() for name in names}
    documented = {key for key in schema_paths() if key.split("/", 1)[0] in BOUND_AREAS}
    assert documented - bound == set(), (
        f"these schema documents have no pydantic binding: {sorted(documented - bound)}"
    )


def test_every_binding_has_a_schema() -> None:
    """No binding claims a contract that has no schema document."""
    for area, names in area_bindings().items():
        for name in names:
            try:
                validate_against_schema(area, name, object())
            except UnknownContractError as missing:  # pragma: no cover - assertion path
                pytest.fail(str(missing))
            except SchemaViolationError:
                continue


def test_every_fixture_names_a_schema_document() -> None:
    """A fixture whose name selects no schema exercises nothing."""
    documented = set(schema_paths())
    referenced = {f"{fixture.area}/{fixture.contract}" for fixture in FIXTURES}
    assert referenced - documented == set(), (
        f"these fixtures name no schema document: {sorted(referenced - documented)}"
    )


def test_every_exercised_contract_is_exercised_from_both_directions() -> None:
    """A contract with fixtures has both kinds; one direction proves nothing.

    Whether every schema document carries fixtures is the owning area
    workstream's responsibility, because only it knows what a meaningful
    instance and a meaningful rejection look like for its documents.
    """
    valid = {f"{f.area}/{f.contract}" for f in FIXTURES if f.expect_valid}
    invalid = {f"{f.area}/{f.contract}" for f in FIXTURES if not f.expect_valid}
    assert valid - invalid == set(), f"no invalid fixture for: {sorted(valid - invalid)}"
    assert invalid - valid == set(), f"no valid fixture for: {sorted(invalid - valid)}"


def test_identifier_registry_matches_the_schema() -> None:
    """The ids enum, the identifiers object, and the Python enum stay one list."""
    from utrace_contracts.ids import IdentifierName
    from utrace_contracts.registry import schema_document

    names = schema_document("ids", "identifier-name.v1")["enum"]
    properties = schema_document("ids", "identifiers.v1")["properties"]
    assert isinstance(names, list)
    assert isinstance(properties, dict)
    assert set(names) == set(properties), (
        "identifier-name.v1 and identifiers.v1 must enumerate exactly the same names"
    )
    assert set(names) == {member.value for member in IdentifierName}, (
        "IdentifierName must mirror identifier-name.v1"
    )


def test_failure_vocabularies_are_one_list_in_both_areas() -> None:
    """The failure vocabularies are one list: the schema's, in one enumeration.

    The envelope schema is the source of truth for these sets, the envelope
    binding turns each into one enumeration, and ``domain`` re-exports that
    enumeration rather than repeating its members.
    """
    from utrace_contracts.domain.lifecycle import (
        CommandDisposition,
        EffectState,
        RetryAdvice,
        TechnicalCategory,
    )
    from utrace_contracts.envelope import failures
    from utrace_contracts.registry import schema_document

    def documented_consts(contract: str, name: str) -> set[str]:
        defs = schema_document("envelope", contract)["$defs"]
        assert isinstance(defs, Mapping)
        branch = defs[name]
        assert isinstance(branch, Mapping)
        alternatives = branch["oneOf"]
        assert isinstance(alternatives, Sequence)
        values: set[str] = set()
        for entry in alternatives:
            assert isinstance(entry, Mapping)
            const = entry["const"]
            assert isinstance(const, str)
            values.add(const)
        return values

    for contract, name, enumeration in (
        ("failure.v1", "technical_category", TechnicalCategory),
        ("failure.v1", "retry_advice", RetryAdvice),
        ("result-body.v1", "command_disposition", CommandDisposition),
        ("result-body.v1", "effect_state", EffectState),
    ):
        documented = documented_consts(contract, name)
        assert documented == {member.value for member in enumeration}, (
            f"{enumeration.__name__} and envelope/{contract} $defs/{name} disagree"
        )
        assert getattr(failures, enumeration.__name__) is enumeration, (
            f"domain re-exports the envelope's {enumeration.__name__}, it does not redefine it"
        )


def test_invalid_fixtures_are_named_by_the_rule_they_violate() -> None:
    """An invalid fixture's case name is its rule, so a failure reads as a sentence."""
    for fixture in FIXTURES:
        if fixture.expect_valid:
            continue
        assert fixture.case and fixture.case.islower(), (
            f"{fixture.path} must name the violated rule in snake_case after '__'"
        )


def test_fixtures_are_formatted_consistently() -> None:
    """Fixtures are two-space indented JSON so review diffs stay readable."""
    for fixture in FIXTURES:
        raw = fixture.path.read_text(encoding="utf-8")
        expected = json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n"
        assert raw == expected, f"{fixture.path} is not canonically formatted"
