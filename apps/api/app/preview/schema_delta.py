"""A candidate's schema change, applied to a spare's own database before the swap.

Contract: docs/arch-components/preview-build.md#spare-candidate-runtimes

A spare installs its dataset when it starts, from the base schema file, before
any candidate exists. A candidate that changes that file would otherwise run
against tables it does not have. The spare therefore compares the candidate's
schema file with the base's statement by statement. When every statement the
candidate adds only adds (a table, an index, a column) and none of the base's is
gone or changed, those statements run in the spare's database in one
transaction, and the running server keeps serving. Any other difference is
destructive: the spare restarts, which installs the dataset afresh from the
candidate's schema file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["SchemaDelta", "SchemaMode", "schema_delta", "split_statements"]

_LINE_COMMENT = re.compile(r"--[^\n]*")
_SPACE = re.compile(r"\s+")
_CREATE_TABLE = re.compile(r"^create table (?:if not exists )?([a-z_][a-z0-9_]*)\b")
_DROP_TABLE_IF_EXISTS = re.compile(r"^drop table if exists ([a-z_][a-z0-9_]*)(?: cascade)?$")
_ADDITIVE = (
    re.compile(r"^create table "),
    re.compile(r"^create (?:unique )?index "),
    re.compile(r"^alter table [a-z_][a-z0-9_]* add column "),
    re.compile(r"^comment on "),
)


class SchemaMode(StrEnum):
    """How a spare's database follows a candidate's schema."""

    UNCHANGED = "unchanged"
    IN_PLACE = "in_place"
    RESTART = "restart"


@dataclass(frozen=True, slots=True)
class SchemaDelta:
    """What a candidate's schema file adds to the base's, and how it can be applied."""

    mode: SchemaMode
    statements: tuple[str, ...]
    """The statements the candidate adds, as written, in the candidate's order."""

    @property
    def digest(self) -> str:
        """The identity of the change, which the spare and its evidence record."""
        body = "\n".join(_normalized(statement) for statement in self.statements)
        return f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"


def schema_delta(*, base: str | None, candidate: str) -> SchemaDelta:
    """Classify the candidate's schema file against the base's.

    A base that has no schema file at that path makes every candidate statement
    new, so the same additive rule decides.
    """
    base_statements = split_statements(base or "")
    candidate_statements = split_statements(candidate)
    known = {_normalized(statement) for statement in base_statements}
    wanted = {_normalized(statement) for statement in candidate_statements}
    added = tuple(
        statement for statement in candidate_statements if _normalized(statement) not in known
    )
    if not added and known <= wanted:
        return SchemaDelta(mode=SchemaMode.UNCHANGED, statements=())
    removed = known - wanted
    created = {
        match.group(1)
        for statement in added
        if (match := _CREATE_TABLE.match(_normalized(statement))) is not None
    }
    additive = not removed and all(_is_additive(statement, created) for statement in added)
    return SchemaDelta(
        mode=SchemaMode.IN_PLACE if additive else SchemaMode.RESTART, statements=added
    )


def split_statements(sql: str) -> tuple[str, ...]:
    """The file's statements, without line comments, split on semicolons.

    A schema file of plain DDL has no semicolon inside a string or a function
    body; one that does is compared as written, and any difference in it is
    treated as destructive.
    """
    stripped = _LINE_COMMENT.sub("", sql)
    return tuple(part.strip() for part in stripped.split(";") if part.strip())


def _is_additive(statement: str, created: set[str]) -> bool:
    normalized = _normalized(statement)
    dropped = _DROP_TABLE_IF_EXISTS.match(normalized)
    if dropped is not None:
        # A guard that drops a table the change itself creates removes nothing.
        return dropped.group(1) in created
    return any(pattern.match(normalized) for pattern in _ADDITIVE)


def _normalized(statement: str) -> str:
    return _SPACE.sub(" ", statement).strip().lower()
