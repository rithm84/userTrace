"""Schema loading, JSON Schema validation, and area discovery.

Two registries live here and neither is a file an area workstream has to edit:

- **Schemas** are discovered from ``schemas/<area>/<name>.v<major>.schema.json``
  on disk and resolved against each other by ``$id``.
- **Bindings** are discovered by importing every ``utrace_contracts.<area>``
  subpackage that exposes a ``contracts`` module with an ``AREA_CONTRACTS``
  mapping. Adding an area means adding a directory, not editing this file.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from utrace_contracts.base import JsonValue
from utrace_contracts.errors import SchemaViolationError, UnknownContractError
from utrace_contracts.locations import SCHEMAS_ROOT

__all__ = [
    "AREA_CONTRACTS_ATTRIBUTE",
    "ContractValidator",
    "area_bindings",
    "schema_areas",
    "schema_document",
    "schema_paths",
    "validate_against_schema",
]

AREA_CONTRACTS_ATTRIBUTE = "AREA_CONTRACTS"
"""The name an area's ``contracts`` module exposes its bindings under."""

ContractValidator = Callable[[object], object]
"""Parses one untrusted decoded-JSON value into that contract's model."""

_SCHEMA_SUFFIX = ".schema.json"

_SchemaResource = bool | Mapping[str, JsonValue]
"""What a JSON Schema document resolves to: a boolean schema or an object schema."""


def schema_paths() -> Mapping[str, Path]:
    """Every schema document on disk, keyed by ``"<area>/<name>.v<major>"``."""
    found: dict[str, Path] = {}
    for path in sorted(SCHEMAS_ROOT.glob(f"*/*{_SCHEMA_SUFFIX}")):
        contract = path.name.removesuffix(_SCHEMA_SUFFIX)
        found[f"{path.parent.name}/{contract}"] = path
    return found


def schema_areas() -> tuple[str, ...]:
    """The contract areas that carry at least one schema document."""
    return tuple(sorted({key.split("/", 1)[0] for key in schema_paths()}))


@cache
def _documents() -> Mapping[str, Mapping[str, JsonValue]]:
    documents: dict[str, Mapping[str, JsonValue]] = {}
    for key, path in schema_paths().items():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError(f"{path} must contain a JSON Schema object, found {type(loaded)}")
        documents[key] = loaded
    return documents


def schema_document(area: str, contract: str) -> Mapping[str, JsonValue]:
    """Return the parsed schema document for ``<area>/<contract>``.

    Raises:
        UnknownContractError: if no such document exists on disk.
    """
    documents = _documents()
    key = f"{area}/{contract}"
    if key not in documents:
        known = tuple(
            sorted(name.split("/", 1)[1] for name in documents if name.startswith(f"{area}/"))
        )
        raise UnknownContractError(area, contract, known)
    return documents[key]


@cache
def _reference_registry() -> Registry[_SchemaResource]:
    registry: Registry[_SchemaResource] = Registry()
    for document in _documents().values():
        registry = registry.with_resource(str(document["$id"]), Resource.from_contents(document))
    return registry


@cache
def _validator(area: str, contract: str) -> Draft202012Validator:
    # ``format`` is an annotation by default in both ecosystems, and the TypeScript
    # suite turns it into an assertion with ajv-formats. Asserting it here too is what
    # keeps the two languages from disagreeing about a uuid or an RFC 3339 timestamp.
    return Draft202012Validator(
        schema_document(area, contract),
        registry=_reference_registry(),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def validate_against_schema(area: str, contract: str, instance: object) -> None:
    """Validate ``instance`` against the ``<area>/<contract>`` JSON Schema.

    The JSON Schema is the conformance boundary between the two bindings. Use
    this at a trust boundary that needs schema-level enforcement; the pydantic
    models enforce the same rules for ordinary application code.

    Raises:
        UnknownContractError: if the schema document does not exist.
        SchemaViolationError: if the instance violates it, with one problem per
            failing keyword.
    """
    errors = sorted(_validator(area, contract).iter_errors(instance), key=lambda e: e.json_path)
    if errors:
        problems = tuple(f"{error.json_path}: {error.message}" for error in errors)
        raise SchemaViolationError(area, contract, problems)


def area_bindings() -> Mapping[str, Mapping[str, ContractValidator]]:
    """Every area's contract bindings, keyed by area then ``"<name>.v<major>"``.

    An area is any ``utrace_contracts`` subpackage whose ``contracts`` module
    exposes ``AREA_CONTRACTS``. Subpackages without one are support code, not
    contract areas.
    """
    import utrace_contracts

    discovered: dict[str, Mapping[str, ContractValidator]] = {}
    for module in pkgutil.iter_modules(utrace_contracts.__path__):
        if not module.ispkg:
            continue
        try:
            contracts = importlib.import_module(f"utrace_contracts.{module.name}.contracts")
        except ModuleNotFoundError:
            continue
        bindings = getattr(contracts, AREA_CONTRACTS_ATTRIBUTE, None)
        if bindings is None:
            raise AttributeError(
                f"utrace_contracts.{module.name}.contracts exists but does not expose "
                f"{AREA_CONTRACTS_ATTRIBUTE}; every contract area must declare its bindings there"
            )
        if not isinstance(bindings, Mapping):
            raise TypeError(
                f"utrace_contracts.{module.name}.contracts.{AREA_CONTRACTS_ATTRIBUTE} must be a "
                f"mapping of '<name>.v<major>' to a validator, found {type(bindings).__name__}"
            )
        discovered[module.name] = bindings
    return discovered
