"""Generate the domain area's JSON Schema documents from the Python bindings.

Run from the repository root:

    uv run --project apps/api python \
        packages/utrace-contracts/python/scripts/export_domain_schemas.py

The bindings are the source of truth; the emitted documents are generated files.
"""

from __future__ import annotations

import json
import sys
from enum import EnumMeta
from typing import Final

from pydantic import BaseModel

from utrace_contracts.domain import lifecycle, records
from utrace_contracts.locations import SCHEMAS_ROOT

SCHEMA_ROOT: Final = SCHEMAS_ROOT / "domain"
SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
ID_PREFIX: Final = "https://utrace.dev/schemas/domain"
MAJOR: Final = 1


def _snake(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index > 0:
            out.append("-")
        out.append(char.lower())
    return "".join(out)


def _enum_document(name: str, member: EnumMeta) -> dict[str, object]:
    doc = (member.__doc__ or "").strip()
    if not doc:
        raise ValueError(
            f"domain enum {name} has no docstring; the generated schema needs a description"
        )
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": f"{ID_PREFIX}/{_snake(name)}/v{MAJOR}",
        "title": name,
        "description": doc.splitlines()[0],
        "type": "string",
        "enum": [item.value for item in member],  # type: ignore[var-annotated]
    }


def _omit_instead_of_null(schema: dict[str, object]) -> None:
    """Rewrite pydantic's nullable properties as omitted optionals.

    ``X | None = None`` is how a Python binding says "this column may be
    absent", and pydantic renders it as ``anyOf: [X, {"type": "null"}]`` plus a
    ``null`` default. The contract omits optional values instead of sending
    ``null``, which ``DomainRecord`` enforces on the way in, so the generated
    document drops the null branch, the null default, and the required entry.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    required = schema.get("required")
    for name, member in properties.items():
        if not isinstance(member, dict):
            continue
        branches = member.get("anyOf")
        if not isinstance(branches, list):
            continue
        kept = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
        if len(kept) == len(branches):
            continue
        member.pop("anyOf")
        member.pop("default", None)
        if len(kept) == 1 and isinstance(kept[0], dict):
            member.update(kept[0])
        else:
            member["anyOf"] = kept
        if isinstance(required, list) and name in required:
            required.remove(name)


def _record_document(name: str, model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    _omit_instead_of_null(schema)
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = f"{ID_PREFIX}/{_snake(name)}/v{MAJOR}"
    return schema


def build_documents() -> dict[str, dict[str, object]]:
    """Return every domain schema document keyed by its file name."""
    documents: dict[str, dict[str, object]] = {}
    for name in lifecycle.__all__:
        member = getattr(lifecycle, name)
        documents[f"{_snake(name)}.v{MAJOR}.schema.json"] = _enum_document(name, member)
    for name in records.__all__:
        model = getattr(records, name)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        documents[f"{_snake(name)}.v{MAJOR}.schema.json"] = _record_document(name, model)
    return documents


def main() -> int:
    """Write every generated document and report how many were emitted."""
    SCHEMA_ROOT.mkdir(parents=True, exist_ok=True)
    documents = build_documents()
    expected = set(documents)
    for path in SCHEMA_ROOT.glob("*.schema.json"):
        if path.name not in expected:
            path.unlink()
    for file_name, document in documents.items():
        (SCHEMA_ROOT / file_name).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    sys.stdout.write(f"wrote {len(documents)} domain schema documents to {SCHEMA_ROOT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
