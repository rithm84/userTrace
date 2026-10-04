"""Finding ranges are checked against the lines the agent's Read tool showed it."""

from app.implementation.investigation import numbered_lines


def test_a_trailing_newline_is_one_more_numbered_line() -> None:
    assert numbered_lines("first\nsecond\n") == ["first", "second", ""]


def test_a_file_without_a_trailing_newline_numbers_only_its_lines() -> None:
    assert numbered_lines("first\nsecond") == ["first", "second"]
