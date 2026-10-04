"""Typed accessors for database rows.

``dict_row`` hands back untyped values. These helpers narrow each column at the
boundary and raise a specific error naming the column when the driver returns
something the caller did not expect, so no ``Any`` leaks into control-plane logic.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

__all__ = [
    "RowTypeError",
    "as_bool",
    "as_datetime",
    "as_int",
    "as_optional_str",
    "as_optional_uuid",
    "as_str",
    "as_uuid",
    "present",
]

class RowTypeError(TypeError):
    """A column held a value of an unexpected type."""

    def __init__(self, column: str, expected: str, value: object) -> None:
        super().__init__(
            f"column '{column}' expected {expected}, got {type(value).__name__}"
        )
        self.column = column
        self.expected = expected


def present(row: Mapping[str, object]) -> dict[str, object]:
    """Drop the row's null columns.

    A nullable column and an omitted contract field mean the same thing, and
    the contract omits an optional value rather than sending ``null``, so a
    record is built from the columns that actually hold a value.
    """
    return {column: value for column, value in row.items() if value is not None}


def _require[T](row: Mapping[str, object], column: str, kind: type[T], label: str) -> T:
    value = row[column]
    if not isinstance(value, kind):
        raise RowTypeError(column=column, expected=label, value=value)
    return value


def as_int(row: Mapping[str, object], column: str) -> int:
    """Return an integer column, rejecting booleans."""
    value = row[column]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RowTypeError(column=column, expected="int", value=value)
    return value


def as_str(row: Mapping[str, object], column: str) -> str:
    """Return a text column."""
    return _require(row, column, str, "str")


def as_optional_str(row: Mapping[str, object], column: str) -> str | None:
    """Return a nullable text column."""
    if row[column] is None:
        return None
    return as_str(row, column)


def as_bool(row: Mapping[str, object], column: str) -> bool:
    """Return a boolean column."""
    return _require(row, column, bool, "bool")


def as_uuid(row: Mapping[str, object], column: str) -> UUID:
    """Return a uuid column."""
    return _require(row, column, UUID, "UUID")


def as_optional_uuid(row: Mapping[str, object], column: str) -> UUID | None:
    """Return a nullable uuid column."""
    if row[column] is None:
        return None
    return as_uuid(row, column)


def as_datetime(row: Mapping[str, object], column: str) -> datetime:
    """Return a timestamptz column."""
    return _require(row, column, datetime, "datetime")
