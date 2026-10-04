"""Shared fixtures for connector tests.

Two kinds of test live here:

- Pure tests for acceptance checks, error mapping, and payload decoding. They run
  everywhere, with no network and no credentials.
- Live smoke tests against the real providers. There are no mocks: a mocked provider
  proves only that the mock matches the guess. Each live test is gated on its own
  credential variables and skips with a reason naming the exact variable that is missing,
  so a skipped run is diagnosable rather than silent.

Environment values come from the repository's `.env.local`, which the worktree symlinks.
The file is never written by tests and its values never reach an assertion message.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _REPOSITORY_ROOT / ".env.local"


def _load_env_file() -> None:
    """Load `KEY=value` lines from `.env.local` without overwriting the real environment.

    A deliberately small parser: it handles the `KEY=value` and `export KEY=value` forms
    with optional surrounding quotes, and ignores everything else. An exported shell
    variable always wins, so a live run can be pointed at other credentials.
    """
    if not _ENV_FILE.exists():
        return
    for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


_load_env_file()


def require_env(*names: str) -> dict[str, str]:
    """Return the named environment values, or skip the test naming the missing variable.

    Every live test calls this first, so the skip reason always identifies what to set.
    """
    values: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name)
        if not value:
            pytest.skip(f"live provider test needs {name}; it is unset")
        values[name] = value
    return values


def optional_env(name: str) -> str | None:
    """Return one environment value, or `None` when it is unset."""
    value = os.environ.get(name)
    return value if value else None


@pytest.fixture(scope="session")
def repository_root() -> Path:
    """Absolute path to the checkout that owns this worktree's `.env.local`."""
    return _REPOSITORY_ROOT


@pytest.fixture
def unique_suffix() -> Iterator[str]:
    """A short suffix that keeps concurrent live runs from colliding on provider names."""
    yield os.urandom(4).hex()
