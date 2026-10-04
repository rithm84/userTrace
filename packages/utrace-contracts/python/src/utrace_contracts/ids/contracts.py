"""Public validators for the ids area and its binding registry."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import TypeAdapter

from utrace_contracts.ids.models import DomainIds, IdentifierName, UuidV7
from utrace_contracts.registry import ContractValidator

__all__ = [
    "AREA_CONTRACTS",
    "validate_identifier_name",
    "validate_identifiers",
    "validate_uuid_v7",
]

_UUID_V7: TypeAdapter[UuidV7] = TypeAdapter(UuidV7)
_IDENTIFIER_NAME: TypeAdapter[IdentifierName] = TypeAdapter(IdentifierName)


def validate_uuid_v7(data: object) -> UuidV7:
    """Validate one canonical lowercase UUIDv7 string."""
    return _UUID_V7.validate_python(data, strict=True)


def validate_identifier_name(data: object) -> IdentifierName:
    """Validate one first-class identifier name.

    Not strict: the wire form of an identifier name is the enum's string value,
    so a member is built from that string rather than required as an instance.
    """
    return _IDENTIFIER_NAME.validate_python(data)


def validate_identifiers(data: object) -> DomainIds:
    """Validate a typed domain identifier object."""
    return DomainIds.from_json(data)


AREA_CONTRACTS: Mapping[str, ContractValidator] = {
    "uuid-v7.v1": validate_uuid_v7,
    "identifier-name.v1": validate_identifier_name,
    "identifiers.v1": validate_identifiers,
}
