"""Versioned uTrace contract bindings.

``utrace_contracts.envelope`` and ``utrace_contracts.ids`` are the base every
other area composes with. Area workstreams add their own subpackage next to
them; nothing here needs editing to register one.

The JSON Schemas under ``packages/utrace-contracts/schemas`` are the source of
truth. These models are the binding application code holds, and the shared
fixtures prove the two agree in both languages.
"""

from utrace_contracts.canonical import DIGEST_ALGORITHM, canonicalize, payload_digest
from utrace_contracts.errors import (
    ContractError,
    DigestMismatchError,
    SchemaViolationError,
    UnknownContractError,
)
from utrace_contracts.locations import FIXTURES_ROOT, SCHEMAS_ROOT
from utrace_contracts.registry import (
    area_bindings,
    schema_areas,
    schema_document,
    schema_paths,
    validate_against_schema,
)

__all__ = [
    "DIGEST_ALGORITHM",
    "FIXTURES_ROOT",
    "SCHEMAS_ROOT",
    "ContractError",
    "DigestMismatchError",
    "SchemaViolationError",
    "UnknownContractError",
    "area_bindings",
    "canonicalize",
    "payload_digest",
    "schema_areas",
    "schema_document",
    "schema_paths",
    "validate_against_schema",
]
