"""Payload canonicalization and digesting.

Messages cross two languages, several transports, and a durable log, so the
digest has to be reproducible from the payload alone. The envelope therefore
pins one algorithm pair: RFC 8785 JSON Canonicalization Scheme, then SHA-256
over its UTF-8 bytes, rendered as lowercase hexadecimal.

JCS is the only canonicalization here because it is the published standard for
exactly this problem: it fixes key ordering by UTF-16 code unit, number
formatting by the ECMAScript ``Number::toString`` algorithm, and string escaping,
which is what makes the Python and TypeScript bindings agree byte for byte.
``fixtures/_digest/vectors.json`` is the shared proof.
"""

from __future__ import annotations

import hashlib

import rfc8785

from utrace_contracts.base import JsonValue

__all__ = ["DIGEST_ALGORITHM", "canonicalize", "payload_digest"]

DIGEST_ALGORITHM = "sha256"
"""The one digest algorithm the envelope contract allows."""


def canonicalize(payload: JsonValue) -> bytes:
    """Return the RFC 8785 canonical UTF-8 bytes of ``payload``.

    Raises:
        rfc8785.CanonicalizationError: if the value is not canonicalizable JSON,
            such as a non-finite float or a non-string object key.
    """
    return rfc8785.dumps(payload)


def payload_digest(payload: JsonValue) -> str:
    """Return the lowercase hex SHA-256 of the canonical form of ``payload``."""
    return hashlib.sha256(canonicalize(payload)).hexdigest()
