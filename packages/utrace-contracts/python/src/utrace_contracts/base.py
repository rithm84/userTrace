"""The pydantic base every contract binding derives from.

The JSON Schemas are the source of truth; these models are the binding that
application code holds. The configuration below keeps the two from drifting:

- ``extra="forbid"`` mirrors ``additionalProperties: false``.
- ``strict=True`` refuses coercion, so a string never silently becomes an
  integer on one side of the wire and not the other.
- ``frozen=True`` matches the contract: an accepted message is immutable.
- ``regex_engine="python-re"`` supports the lookaheads the W3C traceparent
  pattern needs.
- An explicit ``null`` is rejected for every optional field, because the
  contract omits optional values rather than sending ``null``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

__all__ = ["JsonObject", "JsonValue", "SnakeCaseName", "StrictModel", "reject_explicit_null"]

SnakeCaseName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
"""A snake_case contract name: enum values, stage names, reason codes, components."""

type JsonValue = str | int | float | bool | Sequence["JsonValue"] | Mapping[str, "JsonValue"] | None
"""Any decoded-JSON value. Recursive, so a nested payload stays typed end to end."""

type JsonObject = Mapping[str, JsonValue]
"""An open JSON object an area schema constrains, such as a payload or details block."""


def reject_explicit_null(data: object) -> object:
    """Reject ``"field": null``; the contract omits optional values instead.

    Shared by every contract model. An optional value is absent from the object
    rather than present and null, so a binding that quietly accepted ``null``
    would accept instances its own JSON Schema rejects.
    """
    if isinstance(data, Mapping):
        nulls = sorted(str(key) for key, value in data.items() if value is None)
        if nulls:
            raise ValueError(
                f"optional contract fields are omitted, never null; "
                f"received null for: {', '.join(nulls)}"
            )
    return data


class StrictModel(BaseModel):
    """Base for every uTrace contract model."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        regex_engine="python-re",
        validate_default=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null(cls, data: object) -> object:
        return reject_explicit_null(data)

    def as_json(self) -> dict[str, JsonValue]:
        """Return the wire form: omitted optionals, no ``null`` placeholders."""
        dumped: dict[str, JsonValue] = self.model_dump(mode="json", exclude_none=True)
        return dumped

    @classmethod
    def from_json(cls, data: object) -> Self:
        """Validate an untrusted decoded-JSON value into this model."""
        return cls.model_validate(data)
