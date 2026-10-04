"""Fixtures shared by the interview-orchestration tests."""

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def unique_suffix() -> Iterator[str]:
    """A short suffix that keeps concurrent live runs from colliding on provider names."""
    yield os.urandom(4).hex()
