"""The compatibility manifest components advertise during authenticated readiness.

Envelope and payload majors are independent. Additive optional fields may stay
inside a major; removed, renamed, newly required, or semantically changed fields
require a new one. A peer that cannot meet a required major fails readiness with
the ``incompatible_version`` category, because there is no lower-fidelity
substitution.

``supported_majors`` is ordered most preferred first rather than carrying a
separate preference field, so "the preferred major is supported" is structural
and both bindings enforce it without a cross-field rule the JSON Schema could
not express.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from utrace_contracts.base import StrictModel
from utrace_contracts.envelope.values import Producer, Timestamp

__all__ = [
    "PAYLOAD_TYPE_PATTERN",
    "CompatibilityManifest",
    "ContractSupport",
    "DeprecationWindow",
    "MajorSupport",
    "PayloadSupport",
    "PayloadType",
]

PAYLOAD_TYPE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
"""Area-namespaced payload name, for example ``product_signal.created``."""

PayloadType = Annotated[str, StringConstraints(pattern=PAYLOAD_TYPE_PATTERN, max_length=128)]


class DeprecationWindow(StrictModel):
    """One announced window. After ``sunset_at`` the major stops being accepted."""

    deprecated_at: Timestamp
    sunset_at: Timestamp
    replacement_major: Annotated[int, Field(ge=1)]


class MajorSupport(StrictModel):
    """One supported major, with its deprecation window when one is announced."""

    major: Annotated[int, Field(ge=1)]
    deprecation: DeprecationWindow | None = None


class ContractSupport(StrictModel):
    """The majors one contract supports, most preferred first."""

    supported_majors: Annotated[list[MajorSupport], Field(min_length=1, max_length=16)]


class PayloadSupport(StrictModel):
    """Support for one area-namespaced payload type."""

    payload_type: PayloadType
    support: ContractSupport


class CompatibilityManifest(StrictModel):
    """What one component supports. A payload type absent from ``payloads`` is unsupported."""

    component: Producer
    generated_at: Timestamp
    envelope: ContractSupport
    payloads: Annotated[list[PayloadSupport], Field(max_length=512)]
