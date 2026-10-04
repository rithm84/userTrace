"""The account-link redirect may only name the extension's own origin."""

from __future__ import annotations

import pytest

from app.errors import AuthorizationError
from app.extension.installations import validate_extension_redirect_uri

_EXTENSION_ORIGIN = "https://abcdefghijklmnopabcdefghijklmnop.chromiumapp.org"


def test_the_extension_origin_passes_through_unchanged() -> None:
    uri = f"{_EXTENSION_ORIGIN}/link"
    assert validate_extension_redirect_uri(uri) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "http://abcdefghijklmnopabcdefghijklmnop.chromiumapp.org/link",
        "https://utrace.dev/extension/link",
        "https://abcdefghijklmnopabcdefghijklmnop.chromiumapp.org.evil.example/link",
        f"{_EXTENSION_ORIGIN}/link?link_code=forged",
        f"{_EXTENSION_ORIGIN}/link#fragment",
    ],
)
def test_anything_else_is_refused(uri: str) -> None:
    with pytest.raises(AuthorizationError):
        validate_extension_redirect_uri(uri)
