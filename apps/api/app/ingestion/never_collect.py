"""Non-overridable platform exclusions, applied in memory before any write.

Source-side redaction remains mandatory. Ingestion applies the platform
exclusions again as a defensive layer, and it may only remove or restrict data.
A payload containing forbidden material is not written to the journal, object
storage, logs, quarantine, or a content hash: only a safe typed failure record
survives, naming the rule and the structural location, never the value.

A source-policy violation therefore never becomes downstream cleanup that the
system could mistake for proof that the source SDK is healthy.

Contract: docs/arch-components/telemetry-and-evidence.md#durable-receipt-and-acknowledgement
"""

import re
from collections.abc import Mapping, Sequence
from typing import Final

from app.ingestion.errors import NeverCollectViolationError
from app.ingestion.limits import (
    MAX_ARRAY_LENGTH,
    MAX_ATTRIBUTE_VALUE_LENGTH,
    MAX_NESTING_DEPTH,
)

__all__ = [
    "SERVER_EXCLUDED_ATTRIBUTE_KEYS",
    "SERVER_EXCLUDED_ATTRIBUTE_PREFIXES",
    "assert_no_forbidden_material",
    "assert_server_attribute_allowed",
]

#: Field names that never carry an exported value, whatever produced them.
_EXCLUDED_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "set-cookie",
        "password",
        "passphrase",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "private_key",
        "card_number",
        "cvv",
        "cvc",
        "pan",
        "iban",
        "security_answer",
        "request_body",
        "response_body",
        "raw_html",
        "outer_html",
        "inner_html",
        "query_string",
        "url_full",
        "href",
        "stack",
        "stacktrace",
    }
)

#: Attribute keys the server edge never accepts, matching the server SDK's own
#: platform exclusion list.
SERVER_EXCLUDED_ATTRIBUTE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "http.request.body",
        "http.response.body",
        "url.query",
        "url.full",
        "url.path",
        "url.fragment",
        "user_agent.original",
        "user_agent.synthetic.type",
        "client.address",
        "client.port",
        "network.peer.address",
        "network.peer.port",
        "network.local.address",
        "network.local.port",
        "db.query.text",
        "db.query.parameter",
        "db.statement",
        "db.connection_string",
        "db.user",
        "db.query.summary",
        "messaging.message.body",
        "messaging.message.body.size",
        "enduser.id",
        "enduser.role",
        "enduser.scope",
        "code.stacktrace",
        "exception.stacktrace",
        "exception.message",
    }
)

SERVER_EXCLUDED_ATTRIBUTE_PREFIXES: Final[tuple[str, ...]] = (
    "http.request.header.",
    "http.response.header.",
    "http.request.header",
    "http.response.header",
    "db.query.parameter.",
    "messaging.message.header.",
    "aws.",
    "gcp.",
    "azure.",
)

#: Shapes that are credentials wherever they appear in a value.
_CREDENTIAL_SHAPES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("platform.bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}")),
    ("platform.jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("platform.private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("platform.basic_auth_url", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@")),
)

#: Candidate payment-card runs: a digit run standing alone as a token, as the
#: SDKs' own detectors match it. A run inside an alphanumeric token is part of an
#: opaque identifier (a UUID, a trace id, a random hex target id), and one random
#: run in ten passes Luhn, so matching inside tokens refused healthy batches at
#: random. A standalone run only counts once it also passes Luhn, so a long
#: identifier or a nanosecond timestamp is not mistaken for a card number.
_CARD_CANDIDATE: Final[re.Pattern[str]] = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _is_payment_card(digits: str) -> bool:
    """Return whether a 13-to-19 digit run satisfies the Luhn checksum."""
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _normalize_key(key: str) -> str:
    """Lowercase a field name and drop its separators for comparison."""
    return key.lower().replace("-", "_")


def _check_value(value: str, location: str) -> None:
    """Refuse a string value that carries platform-excluded material."""
    for rule_id, pattern in _CREDENTIAL_SHAPES:
        if pattern.search(value) is not None:
            raise NeverCollectViolationError(rule_id=rule_id, location=location)
    for candidate in _CARD_CANDIDATE.findall(value):
        digits = re.sub(r"[ -]", "", candidate)
        if 13 <= len(digits) <= 19 and _is_payment_card(digits):
            raise NeverCollectViolationError(
                rule_id="platform.payment_card", location=location
            )
    if len(value) > MAX_ATTRIBUTE_VALUE_LENGTH:
        raise NeverCollectViolationError(
            rule_id="platform.unbounded_value", location=location
        )


def assert_no_forbidden_material(value: object, location: str, depth: int) -> None:
    """Walk one decoded payload and refuse forbidden fields, shapes, and shapes' sizes.

    The walk is recursive over the decoded JSON structure, so nesting cannot hide
    a forbidden field behind a wrapper object. The bound checks run during the
    same walk, before anything is allocated for a normalized record.
    """
    if depth > MAX_NESTING_DEPTH:
        raise NeverCollectViolationError(
            rule_id="platform.nesting_depth", location=location
        )
    match value:
        case Mapping():
            for key, child in value.items():
                name = str(key)
                if _normalize_key(name) in _EXCLUDED_FIELD_NAMES:
                    raise NeverCollectViolationError(
                        rule_id="platform.excluded_field", location=f"{location}.{name}"
                    )
                assert_no_forbidden_material(child, f"{location}.{name}", depth + 1)
        case str():
            _check_value(value, location)
        case Sequence():
            if len(value) > MAX_ARRAY_LENGTH:
                raise NeverCollectViolationError(
                    rule_id="platform.array_length", location=location
                )
            for index, child in enumerate(value):
                assert_no_forbidden_material(child, f"{location}[{index}]", depth + 1)
        case _:
            return


def assert_server_attribute_allowed(key: str, location: str) -> None:
    """Refuse a span attribute key that no client policy can re-enable."""
    if key in SERVER_EXCLUDED_ATTRIBUTE_KEYS:
        raise NeverCollectViolationError(
            rule_id="platform.excluded_attribute", location=f"{location}.{key}"
        )
    for prefix in SERVER_EXCLUDED_ATTRIBUTE_PREFIXES:
        if key.startswith(prefix):
            raise NeverCollectViolationError(
                rule_id="platform.excluded_attribute_prefix",
                location=f"{location}.{key}",
            )
