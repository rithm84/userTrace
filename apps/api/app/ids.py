"""UUIDv7 identifier generation.

Every first-class uTrace entity has its own opaque UUIDv7. Identifiers are
generated in Python, never by a column default, so the value is known before the
transaction opens and can be carried into Events, outbox work, and audit records.

RFC 9562 layout: 48-bit big-endian Unix milliseconds, version 7, 12 random bits,
the IETF variant, and 62 further random bits. Time ordering keeps index locality;
the random tail keeps identifiers unguessable.
"""

import secrets
import time
from uuid import UUID

__all__ = ["uuid7"]

_VERSION_7 = 0x70
_VARIANT_IETF = 0x80


def uuid7() -> UUID:
    """Return a fresh time-ordered UUIDv7."""
    unix_ts_ms = time.time_ns() // 1_000_000
    entropy = secrets.token_bytes(10)
    raw = bytearray(16)
    raw[0:6] = unix_ts_ms.to_bytes(6, "big")
    raw[6] = _VERSION_7 | (entropy[0] & 0x0F)
    raw[7] = entropy[1]
    raw[8] = _VARIANT_IETF | (entropy[2] & 0x3F)
    raw[9:16] = entropy[3:10]
    return UUID(bytes=bytes(raw))
