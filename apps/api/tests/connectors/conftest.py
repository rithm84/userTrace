"""Shared helpers for connector tests.

Two kinds of test live here:

- Pure tests for acceptance checks, error mapping, and payload decoding. They run
  everywhere, with no network and no credentials.
- Live smoke tests against the real providers. There are no mocks: a mocked provider
  proves only that the mock matches the guess. Each live test is gated on its own
  credential variables and skips with a reason naming the exact variable that is missing,
  so a skipped run is diagnosable rather than silent.

Environment values come from the repository's `.env.local`, which `tests/conftest.py`
loads.
"""

from __future__ import annotations

import os

import pytest


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
