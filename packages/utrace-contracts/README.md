# uTrace contracts

The versioned contract package defined in
[cross-component contracts](../../docs/arch-components/cross-component-contracts.md#versioning-and-compatibility):
JSON Schema definitions, Python and TypeScript bindings, runtime validators,
compatibility manifests, and canonical fixtures shared by every uTrace component.

The JSON Schemas are the source of truth. The two bindings are how application
code holds them, and the shared fixtures prove the bindings agree — a binding
must accept exactly what its schema accepts and reject exactly what its schema
rejects, in both languages.

## Layout

```
schemas/<area>/<name>.v<major>.schema.json   JSON Schema 2020-12 sources (one document per message or record)
fixtures/<area>/valid/*.json                 canonical instances every binding must accept
fixtures/<area>/invalid/*.json               instances every binding must reject, named by the violated rule
fixtures/_digest/vectors.json                cross-language canonicalization and digest vectors
python/src/utrace_contracts/                 package `utrace_contracts` (pydantic v2 models + validators)
typescript/                                  package `@utrace/contracts` (zod 4 schemas + inferred types)
```

Fixtures are flat inside a bucket. A file's name up to `__` selects the schema,
so a directory carries no meaning of its own and neither suite has to recurse.

The Python package sits under `src/` because an editable install of a flat
layout puts the project directory itself on `sys.path`, where this package's
`tests` directory shadows the `tests` package of every project that depends on
it. It ships `py.typed`, so a dependent reads the real annotations rather than
following untyped imports.

The `envelope` and `ids` areas are the base every other area composes with. They
are owned by the contracts workstream; every other area is owned by the
workstream that builds it.

## Where bindings live

`envelope` and `ids` ship their bindings here, in both languages. An area owned
by the workstream that builds it keeps its bindings with its consumer, beside
the code that holds them:

| Area | Bindings |
| --- | --- |
| `browser-observation` | `packages/utrace-browser-sdk/src/` |
| `server-activation` | `packages/utrace-server-sdk/src/contracts/` |
| `extension-protocol` | `apps/browser-extension/src/protocol/` |
| `preview` | `apps/preview-front-door/lib/contracts.ts` |
| `domain` | `python/src/utrace_contracts/domain/`, which generates that area's schemas |

The conformance suites here therefore prove two different things. For every
area they prove that the JSON Schema and the fixtures agree, in both languages,
over the same files. For an area whose bindings ship here they also prove that
the binding accepts and rejects exactly what its schema does. An area whose
bindings live with its consumer proves that in the consumer's own suite against
these same fixtures, which is what the browser SDK, the server SDK, the browser
extension, and the preview front door each do.

## Commands

```bash
# Python bindings, conformance suite, and the schema lint
cd python && uv sync
uv run pytest
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run utrace-schema-lint

# TypeScript bindings and conformance suite
cd typescript && npm install
npm test
npm run typecheck
npm run lint:schemas   # delegates to the Python entry point; one implementation, two toolchains
```

## Conventions for area schemas

The schema lint enforces the mechanical rules; the rest are review rules.

- `$id` is `https://utrace.dev/schemas/<area>/<name>/v<major>` and must match the
  file path. `$schema` is draft 2020-12. Every document has a `title` and a
  `description`.
- Every object schema sets `additionalProperties: false` and lists `required`
  explicitly, even when it is empty. A schema that narrows a `$ref`ed base with
  its own `properties` sets `unevaluatedProperties: false` instead, and so does
  a union root whose branches evaluate the properties.
- A property a branch requires is declared in that branch, as
  `"then": {"required": ["x"], "properties": {"x": true}}`. The rule reads the
  same either way, and ajv's strict mode will not compile the document without
  it.
- An object with no named members whose values are constrained by an
  `additionalProperties` schema is a map. A map has nothing for `required` to
  list and nothing for `additionalProperties: false` to close; its value schema
  is what closes it. An object that declares `properties` is a record and stays
  under the closure rule even when it also carries an `additionalProperties`
  schema.
- A slot an area schema is meant to fill — an envelope payload, a failure's
  `details`, a Result's `output` — is marked
  `"$comment": "extension-point: <why>"` and declares no properties of its own.
  The area schema that fills it closes and documents its own object, so the
  exemption moves the rule rather than waiving it.
- Optional fields are omitted, never `null`. `null` must not appear in any
  `type`, and both bindings reject an explicit `null` for an omitted field.
  A nullable property is not an escape hatch for "the column may be empty": a
  read projection drops its null columns before it builds the record.
- `format` is an assertion, not an annotation. The TypeScript suite turns it on
  with `ajv-formats` and the Python validator passes
  `Draft202012Validator.FORMAT_CHECKER`, because a `uuid` or an RFC 3339
  timestamp that one language rejects and the other accepts is exactly the
  divergence these fixtures exist to catch.
- Identifiers are UUIDv7 strings in lowercase canonical form
  (`ids/uuid-v7.v1`). Provider identifiers are scoped strings inside provider
  references, never product identity.
- Timestamps are RFC 3339 UTC strings with a literal `Z`. Amounts are integer
  minor units.
- Enumerations use the exact values from the docs. The rule governs vocabulary
  uTrace names, and the docs name it two ways: plain snake_case for lifecycle
  and reason vocabularies (`gate_pending`, `candidate_ready`, `stale_target`),
  and dot-separated snake_case segments for observation and frame types
  (`request.started`, `baseline.snapshot`, `utrace.frame.hello`). Both are
  accepted, because the alternative is renaming values the architecture spells
  out. Kebab-case and camelCase are not: they are a different spelling of the
  same vocabulary and get renamed.
- A closed value uTrace does not get to spell carries
  `"$comment": "fixed-literal: <source>"` naming what fixes it — an external
  standard's token (`GET` from RFC 9110, `ES256` from RFC 7518), a URL path, a
  decimal contract major. Like `extension-point:`, the marker moves the rule to
  a citation rather than waiving it, and it never covers a value uTrace names.
- A documented enumeration is written as `oneOf` of `const` entries with a
  `title` and `description` each; a bare `enum` array is fine when the values
  need no explanation.
- A rule the JSON Schema cannot express does not belong in a binding either.
  Semantic invariants beyond JSON Schema's reach — uniqueness across a list,
  ordering between two timestamps — belong to the owning controller, because a
  binding that is stricter than its schema makes the two languages disagree.
  Where a structural encoding can carry the invariant instead, prefer it: for
  example `supported_majors` is ordered most preferred first rather than
  carrying a separate `preferred_major` field.

## Digests

The envelope pins one algorithm pair: RFC 8785 JSON Canonicalization Scheme over
the payload, then SHA-256 of its UTF-8 bytes as lowercase hexadecimal. JCS is the
published standard for this problem — it fixes key ordering by UTF-16 code unit,
number formatting by the ECMAScript `Number::toString` algorithm, and string
escaping — which is what lets the two bindings agree byte for byte.

`fixtures/_digest/vectors.json` carries canonical forms written by hand from
RFC 8785 with their digests. Both suites check their canonicalizer against that
file, so they are checked against the standard rather than against each other's
library. Python uses `rfc8785`; TypeScript uses `canonicalize`.

## Adding an area

Area workstreams add schemas, fixtures, and bindings without editing any file the
contracts workstream owns. Discovery is by directory layout in both languages.

1. **Schemas.** Add `schemas/<area>/<name>.v<major>.schema.json` following the
   conventions above. Compose the envelope with
   `{"$ref": "https://utrace.dev/schemas/envelope/envelope/v1"}` and constrain
   `payload`, or `$ref` the value objects in that document's `$defs`.
2. **Fixtures.** Add `fixtures/<area>/valid/<name>.v<major>__<case>.json` and
   `fixtures/<area>/invalid/<name>.v<major>__<rule_violated>.json`, flat inside
   the bucket. The name up to `__` selects the schema; the part after it names
   the case or the rule the instance breaks, in snake_case. A document you
   exercise at all is exercised from both directions, because one direction
   proves nothing. Fixtures are two-space JSON. A directory under `fixtures/`
   whose name starts with `_` is shared test data, not a contract area.
3. **Bindings.** Either ship them here or keep them with the consumer, and say
   which in the table above. Here means
   `python/src/utrace_contracts/<area>/` with a `contracts.py` exposing
   `AREA_CONTRACTS: Mapping[str, ContractValidator]` keyed by
   `"<name>.v<major>"`, and `typescript/src/<area>/` with a `contracts.ts`
   exporting `areaContracts: ContractBindings` keyed the same way;
   `utrace_contracts.registry.area_bindings()` finds any subpackage with a
   `contracts` module and the TypeScript suite globs `src/*/contracts.ts`. An
   area that ships bindings here binds every one of its schema documents.
   With the consumer means a conformance test in that package that runs these
   fixtures against the binding and the schema, exactly as this suite does.
4. Run both suites and the schema lint, plus the owning package's suite when
   the bindings live there.

Envelope and payload majors are independent. Additive optional fields may stay
inside a major; removed, renamed, newly required, or semantically changed fields
require a new `.v<major+1>` document alongside the old one, plus a deprecation
window in the [compatibility manifest](schemas/envelope/compatibility-manifest.v1.schema.json).
