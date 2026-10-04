"""The schema lint that enforces this package's README conventions.

The conventions exist because two independently written bindings and nine
workstreams share these documents. A schema that is open, that leaves ``required``
implicit, or that spells an enum value differently from the rest of the system
becomes a silent divergence between Python and TypeScript, so the lint is a hard
gate rather than advice.

Run it as ``uv run utrace-schema-lint`` or ``npm run lint:schemas``.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from utrace_contracts.locations import SCHEMAS_ROOT

__all__ = ["Violation", "lint_schemas", "main"]

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
ID_PATTERN = re.compile(r"^https://utrace\.dev/schemas/([a-z][a-z0-9-]*)/([a-z][a-z0-9-]*)/v(\d+)$")
FILE_PATTERN = re.compile(r"^([a-z][a-z0-9-]*)\.v(\d+)\.schema\.json$")
VOCABULARY_VALUE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
"""A uTrace vocabulary value: snake_case, or dot-separated snake_case segments.

The docs name observation and frame types with dots -- ``request.started``,
``baseline.snapshot``, ``utrace.frame.hello`` -- so a dot separates segments and
each segment is snake_case. Kebab-case and camelCase are still rejected.
"""

_CLOSURE_KEYWORDS = ("additionalProperties", "unevaluatedProperties")

FIXED_LITERAL_PREFIX = "fixed-literal:"
"""``$comment`` prefix that marks closed values uTrace does not get to name.

The vocabulary rule governs names uTrace invents. A subschema whose closed
values are spelled by something else -- an external standard's token
(``GET`` from RFC 9110, ``ES256`` from RFC 7518), a URL path, or a decimal
version literal -- carries this marker and names that source, so the exemption
is a documented citation rather than a silent waiver. It never covers a value
uTrace names itself.
"""

EXTENSION_POINT_PREFIX = "extension-point:"
"""``$comment`` prefix that marks a deliberately open object.

Only a slot an area schema is expected to constrain may carry it, such as an
envelope payload or a typed failure's details block. The area schema that fills
the slot still has to close and document its own object, so the exemption moves
the rule rather than waiving it.
"""


@dataclass(frozen=True, slots=True)
class Violation:
    """One convention the schema corpus breaks, reported with its JSON pointer."""

    path: Path
    pointer: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}#{self.pointer} [{self.rule}] {self.detail}"


def _subschemas(node: object, pointer: str) -> Iterator[tuple[str, Mapping[str, object]]]:
    """Yield every subschema object with its JSON pointer, depth first."""
    if isinstance(node, Mapping):
        yield pointer, node
        for key, value in node.items():
            yield from _subschemas(value, f"{pointer}/{key}")
    elif isinstance(node, Sequence) and not isinstance(node, str | bytes):
        for index, value in enumerate(node):
            yield from _subschemas(value, f"{pointer}/{index}")


def _check_document_identity(path: Path, document: Mapping[str, object]) -> list[Violation]:
    violations: list[Violation] = []
    schema = document.get("$schema")
    if schema != DRAFT_2020_12:
        violations.append(
            Violation(path, "", "draft", f"$schema must be {DRAFT_2020_12!r}, found {schema!r}")
        )

    file_match = FILE_PATTERN.match(path.name)
    if file_match is None:
        violations.append(
            Violation(path, "", "filename", "file must be named <name>.v<major>.schema.json")
        )

    identifier = document.get("$id")
    id_match = ID_PATTERN.match(identifier) if isinstance(identifier, str) else None
    if id_match is None:
        violations.append(
            Violation(
                path,
                "",
                "id-pattern",
                "$id must be https://utrace.dev/schemas/<area>/<name>/v<major>, "
                f"found {identifier!r}",
            )
        )
    elif file_match is not None:
        area, name, major = id_match.groups()
        expected = (path.parent.name, file_match.group(1), file_match.group(2))
        if (area, name, major) != expected:
            violations.append(
                Violation(
                    path,
                    "",
                    "id-path",
                    f"$id says {area}/{name}/v{major} but the file path says "
                    f"{expected[0]}/{expected[1]}/v{expected[2]}",
                )
            )

    for field in ("title", "description"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append(
                Violation(path, "", "documented", f"the document needs a non-empty {field}")
            )
    return violations


def _has_marker(node: Mapping[str, object], prefix: str) -> bool:
    comment = node.get("$comment")
    return isinstance(comment, str) and comment.startswith(prefix)


def _is_extension_point(node: Mapping[str, object]) -> bool:
    return _has_marker(node, EXTENSION_POINT_PREFIX)


def _is_fixed_literal(node: Mapping[str, object]) -> bool:
    return _has_marker(node, FIXED_LITERAL_PREFIX)


def _is_constrained_map(node: Mapping[str, object]) -> bool:
    """Is this object a map of dynamic keys to one constrained value schema?

    A map names no members, so there is nothing for ``required`` to list and
    nothing for ``additionalProperties: false`` to close; what makes it closed
    is that every value is validated by the schema under ``additionalProperties``.
    An object that declares ``properties`` is a record and stays under the
    closure rule, even when it also carries an ``additionalProperties`` schema.
    """
    return "properties" not in node and isinstance(node.get("additionalProperties"), Mapping)


def _check_subschema(path: Path, pointer: str, node: Mapping[str, object]) -> list[Violation]:
    violations: list[Violation] = []
    has_properties = "properties" in node

    if node.get("type") == "object" and _is_extension_point(node):
        if has_properties:
            violations.append(
                Violation(
                    path,
                    pointer,
                    "extension-point",
                    "an open extension point declares no properties of its own; "
                    "the area schema that fills it owns them",
                )
            )
    elif node.get("type") == "object" and _is_constrained_map(node):
        pass
    elif node.get("type") == "object":
        closures = [node[keyword] for keyword in _CLOSURE_KEYWORDS if keyword in node]
        if not closures:
            violations.append(
                Violation(
                    path,
                    pointer,
                    "closed",
                    "an object schema must declare additionalProperties: false "
                    "or unevaluatedProperties: false",
                )
            )
        elif any(closure is not False for closure in closures):
            violations.append(
                Violation(path, pointer, "closed", "object closure keywords must be false")
            )
        if "required" not in node:
            violations.append(
                Violation(
                    path,
                    pointer,
                    "required-explicit",
                    "an object schema must list required explicitly, even when it is empty",
                )
            )
    elif "$ref" in node and has_properties and node.get("unevaluatedProperties") is not False:
        violations.append(
            Violation(
                path,
                pointer,
                "closed",
                "a schema that extends a $ref with properties must declare "
                "unevaluatedProperties: false",
            )
        )

    if "type" in node:
        declared = node["type"]
        types = declared if isinstance(declared, list) else [declared]
        if "null" in types:
            violations.append(
                Violation(
                    path,
                    pointer,
                    "no-null",
                    "optional values are omitted, never null; remove the null type",
                )
            )

    if not _is_fixed_literal(node):
        for value in _enumerated_strings(node):
            if not VOCABULARY_VALUE.match(value):
                violations.append(
                    Violation(
                        path,
                        pointer,
                        "snake-case-enum",
                        f"enumerated value {value!r} is not a snake_case vocabulary value",
                    )
                )
    return violations


def _enumerated_strings(node: Mapping[str, object]) -> Iterator[str]:
    """Yield the closed string values a subschema fixes, from ``enum`` and ``const``."""
    enum = node.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, str | bytes):
        for value in enum:
            if isinstance(value, str):
                yield value
    const = node.get("const")
    if isinstance(const, str):
        yield const


def lint_schemas(root: Path) -> list[Violation]:
    """Return every convention violation in the schema corpus under ``root``."""
    violations: list[Violation] = []
    paths = sorted(root.glob("*/*.schema.json"))
    if not paths:
        raise FileNotFoundError(f"no schema documents found under {root}")

    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            violations.append(
                Violation(path, "", "document", "a schema document must be a JSON object")
            )
            continue
        violations.extend(_check_document_identity(path, document))
        for pointer, node in _subschemas(document, ""):
            violations.extend(_check_subschema(path, pointer, node))
    return violations


def main() -> int:
    """Print every violation and exit non-zero when the corpus breaks a convention."""
    violations = lint_schemas(SCHEMAS_ROOT)
    for violation in violations:
        print(violation, file=sys.stderr)
    checked = len(sorted(SCHEMAS_ROOT.glob("*/*.schema.json")))
    if violations:
        print(
            f"schema lint failed: {len(violations)} violation(s) across {checked} document(s)",
            file=sys.stderr,
        )
        return 1
    print(f"schema lint passed: {checked} document(s) in {SCHEMAS_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
