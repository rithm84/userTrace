"""Repository path matching, shared by the gate screen and the diff scope check.

One implementation so a path the gate permitted is the same path the accepted
diff is measured against. Paths are repository-relative and POSIX-separated;
anything that escapes the repository root is refused outright rather than
normalized into something that looks acceptable.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatchcase
from posixpath import normpath

__all__ = ["PathEscapesRepositoryError", "matches_any_pattern", "normalize", "within_any_prefix"]


class PathEscapesRepositoryError(ValueError):
    """A path leaves the repository root, is absolute, or is empty."""

    def __init__(self, path: str) -> None:
        super().__init__(f"'{path}' is not a repository-relative path inside the repository")
        self.path = path


def normalize(path: str) -> str:
    """Return the repository-relative POSIX path, or refuse one that escapes it."""
    if not path or path.startswith("/") or "\\" in path:
        raise PathEscapesRepositoryError(path)
    collapsed = normpath(path)
    if collapsed == "." or collapsed.startswith("../"):
        raise PathEscapesRepositoryError(path)
    return collapsed


def within_any_prefix(path: str, prefixes: Sequence[str]) -> bool:
    """Whether the path sits at or under one of the prefixes.

    A prefix is a directory (`src/lib`) or an exact file (`package.json`). A
    prefix never matches a longer sibling name: `src/lib` does not cover
    `src/library/index.ts`.
    """
    candidate = normalize(path)
    for raw in prefixes:
        prefix = normalize(raw)
        if candidate == prefix or candidate.startswith(f"{prefix}/"):
            return True
    return False


def matches_any_pattern(path: str, patterns: Sequence[str]) -> bool:
    """Whether the path matches one of the case-sensitive glob patterns."""
    candidate = normalize(path)
    return any(fnmatchcase(candidate, pattern) for pattern in patterns)
