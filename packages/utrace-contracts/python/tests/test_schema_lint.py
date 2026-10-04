"""The schema lint is a gate, so it runs in the test suite too.

It also has to actually catch the conventions it claims to: a lint that passes
on a broken corpus is worse than no lint, so each rule is exercised against a
document that breaks it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utrace_contracts.locations import SCHEMAS_ROOT
from utrace_contracts.schema_lint import lint_schemas, main

_WELL_FORMED = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://utrace.dev/schemas/example/thing/v1",
    "title": "Thing",
    "description": "A well formed document used to exercise the lint.",
    "type": "object",
    "properties": {"state": {"enum": ["ready", "blocked"]}},
    "required": ["state"],
    "additionalProperties": False,
}


def _write(tmp_path: Path, document: object) -> Path:
    area = tmp_path / "example"
    area.mkdir(parents=True, exist_ok=True)
    (area / "thing.v1.schema.json").write_text(json.dumps(document), encoding="utf-8")
    return tmp_path


def test_repository_schemas_pass_the_lint() -> None:
    assert lint_schemas(SCHEMAS_ROOT) == []
    assert main() == 0


def test_well_formed_document_passes(tmp_path: Path) -> None:
    assert lint_schemas(_write(tmp_path, _WELL_FORMED)) == []


@pytest.mark.parametrize(
    ("rule", "mutation"),
    [
        ("draft", {"$schema": "http://json-schema.org/draft-07/schema#"}),
        ("id-pattern", {"$id": "https://example.com/thing"}),
        ("id-path", {"$id": "https://utrace.dev/schemas/other/thing/v1"}),
        ("documented", {"description": ""}),
        ("closed", {"additionalProperties": True}),
        ("snake-case-enum", {"properties": {"state": {"enum": ["Ready"]}}}),
        ("snake-case-enum", {"properties": {"state": {"enum": ["stale-target"]}}}),
        ("snake-case-enum", {"properties": {"state": {"const": "staleTarget"}}}),
        ("no-null", {"properties": {"state": {"type": ["string", "null"]}}}),
    ],
    ids=lambda value: value if isinstance(value, str) else "mutation",
)
def test_each_rule_catches_its_violation(
    tmp_path: Path, rule: str, mutation: dict[str, object]
) -> None:
    violations = lint_schemas(_write(tmp_path, {**_WELL_FORMED, **mutation}))
    assert rule in {violation.rule for violation in violations}, (
        f"expected rule {rule} to fire, got {[v.rule for v in violations]}"
    )


def test_missing_closure_and_required_are_both_reported(tmp_path: Path) -> None:
    document = {
        key: value
        for key, value in _WELL_FORMED.items()
        if key not in {"additionalProperties", "required"}
    }
    rules = {violation.rule for violation in lint_schemas(_write(tmp_path, document))}
    assert {"closed", "required-explicit"} <= rules


def test_ref_extension_must_declare_unevaluated_properties(tmp_path: Path) -> None:
    document = {
        "$schema": _WELL_FORMED["$schema"],
        "$id": _WELL_FORMED["$id"],
        "title": "Thing",
        "description": "Extends a base without closing itself.",
        "$ref": "https://utrace.dev/schemas/envelope/envelope/v1",
        "properties": {"family": {"const": "event"}},
    }
    rules = {violation.rule for violation in lint_schemas(_write(tmp_path, document))}
    assert "closed" in rules


def test_extension_point_must_not_declare_properties(tmp_path: Path) -> None:
    document = {
        **_WELL_FORMED,
        "properties": {
            "payload": {
                "type": "object",
                "$comment": "extension-point: the area schema constrains this.",
                "properties": {"leaked": {"type": "string"}},
            }
        },
        "required": [],
    }
    rules = {violation.rule for violation in lint_schemas(_write(tmp_path, document))}
    assert "extension-point" in rules


@pytest.mark.parametrize("value", ["request.started", "utrace.frame.hello", "gate_pending"])
def test_dotted_vocabulary_values_are_accepted(tmp_path: Path, value: str) -> None:
    """The docs name observation and frame types with dots; they are not renamed."""
    document = {**_WELL_FORMED, "properties": {"state": {"const": value}}}
    assert lint_schemas(_write(tmp_path, document)) == []


def test_fixed_literal_marker_exempts_values_utrace_does_not_name(tmp_path: Path) -> None:
    """An external standard's token keeps its own spelling when the marker cites it."""
    document = {
        **_WELL_FORMED,
        "properties": {
            "state": {
                "$comment": "fixed-literal: RFC 9110 method tokens.",
                "enum": ["GET", "POST"],
            }
        },
    }
    assert lint_schemas(_write(tmp_path, document)) == []


def test_constrained_map_is_closed_by_its_value_schema(tmp_path: Path) -> None:
    """A map names no members, so its value schema is what closes it."""
    document = {
        **_WELL_FORMED,
        "properties": {
            "state": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "maxProperties": 8,
            }
        },
    }
    assert lint_schemas(_write(tmp_path, document)) == []


def test_record_with_an_open_tail_is_still_reported(tmp_path: Path) -> None:
    """Declaring properties makes it a record, and a record has to close."""
    document = {
        **_WELL_FORMED,
        "properties": {
            "state": {
                "type": "object",
                "properties": {"named": {"type": "string"}},
                "required": [],
                "additionalProperties": {"type": "string"},
            }
        },
    }
    rules = {violation.rule for violation in lint_schemas(_write(tmp_path, document))}
    assert "closed" in rules


def test_empty_corpus_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        lint_schemas(tmp_path)
