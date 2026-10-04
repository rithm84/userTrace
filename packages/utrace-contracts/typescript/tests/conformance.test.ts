/**
 * Conformance: the JSON Schemas, the bindings, and the fixtures must all agree.
 *
 * The JSON Schema is the conformance boundary between the two languages, so a
 * binding must accept exactly what its schema accepts and reject exactly what
 * its schema rejects. Any rule a binding enforces but the schema cannot express
 * would make the Python and TypeScript packages disagree in production, which is
 * why these tests assert equality rather than "at least as strict".
 *
 * The suite runs the same fixtures as the Python suite, so passing both is what
 * proves the two languages accept and reject the same instances.
 */

import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import { IDENTIFIER_NAMES } from "../src/ids/ids.js";
import type { JsonValue } from "../src/json.js";
import {
  discoverBindings,
  discoverFixtures,
  discoverSchemas,
  loadInstance,
  type Fixture,
} from "./discovery.js";

const fixtures = discoverFixtures();
const schemas = discoverSchemas();
const bindings = discoverBindings();

/**
 * Areas whose bindings this package ships.
 *
 * The `envelope` and `ids` areas are the base every other area composes with,
 * so their bindings live here. An area owned by the workstream that builds it
 * keeps its bindings with its consumer and runs these same fixtures against
 * them in that package's own suite; what this package proves for such an area
 * is that the JSON Schema and the fixtures agree.
 */
const boundFixtures = fixtures.filter((fixture) => bindings.has(fixture.area));

// ajv's strict mode is kept on as a second opinion about the corpus, with one
// exception: strictTypes demands a sibling `type` next to every type-specific
// keyword, which a schema that narrows a $ref'd base cannot supply without
// copying the base. The project's own conventions are enforced by
// `npm run lint:schemas`.
const ajv = new Ajv2020({
  strict: true,
  strictTypes: false,
  allErrors: true,
  allowUnionTypes: true,
});
addFormats(ajv);
for (const document of schemas.values()) {
  ajv.addSchema(document as object);
}

function validatorFor(fixture: Fixture): ValidateFunction {
  const key = `${fixture.area}/${fixture.contract}`;
  const document = schemas.get(key);
  if (document === undefined) {
    throw new Error(
      `${fixture.path} references ${key}, for which there is no schema document on disk`,
    );
  }
  const id = (document as { $id: string }).$id;
  const validate = ajv.getSchema(id);
  if (validate === undefined) {
    throw new Error(`ajv did not register ${id}`);
  }
  return validate;
}

describe("json schema", () => {
  it.each(fixtures.map((fixture) => [fixture.label, fixture] as const))(
    "%s",
    (_label, fixture) => {
      const validate = validatorFor(fixture);
      const accepted = validate(loadInstance(fixture));
      expect(accepted, JSON.stringify(validate.errors)).toBe(fixture.expectValid);
    },
  );
});

describe("zod bindings", () => {
  it.each(boundFixtures.map((fixture) => [fixture.label, fixture] as const))(
    "%s",
    (_label, fixture) => {
      const areaBindings = bindings.get(fixture.area);
      const parse = areaBindings?.[fixture.contract];
      expect(
        parse,
        `${fixture.area} fixtures reference ${fixture.contract}, which is not bound in ` +
          `src/${fixture.area}/contracts.ts`,
      ).toBeDefined();
      const instance = loadInstance(fixture);
      if (fixture.expectValid) {
        expect(() => parse?.(instance)).not.toThrow();
      } else {
        expect(() => parse?.(instance)).toThrow();
      }
    },
  );
});

describe("coverage", () => {
  it("binds every schema of an area whose bindings live here", () => {
    const bound = new Set(
      [...bindings.entries()].flatMap(([area, names]) =>
        Object.keys(names).map((name) => `${area}/${name}`),
      ),
    );
    const unbound = [...schemas.keys()].filter(
      (key) => bindings.has(key.split("/")[0] ?? "") && !bound.has(key),
    );
    expect(unbound, "these schema documents have no zod binding").toEqual([]);
  });

  it("claims no contract without a schema document", () => {
    const claimed = [...bindings.entries()].flatMap(([area, names]) =>
      Object.keys(names).map((name) => `${area}/${name}`),
    );
    const undocumented = claimed.filter((key) => !schemas.has(key));
    expect(undocumented, "these bindings have no schema document").toEqual([]);
  });

  it("names a schema document from every fixture", () => {
    const referenced = [...new Set(fixtures.map((f) => `${f.area}/${f.contract}`))].sort();
    expect(referenced.filter((key) => !schemas.has(key))).toEqual([]);
  });

  it("exercises every contract that has fixtures from both directions", () => {
    // Whether every schema document carries fixtures is the owning area
    // workstream's responsibility; one direction alone proves nothing.
    const valid = new Set(
      fixtures.filter((f) => f.expectValid).map((f) => `${f.area}/${f.contract}`),
    );
    const invalid = new Set(
      fixtures.filter((f) => !f.expectValid).map((f) => `${f.area}/${f.contract}`),
    );
    expect([...valid].filter((key) => !invalid.has(key)).sort()).toEqual([]);
    expect([...invalid].filter((key) => !valid.has(key)).sort()).toEqual([]);
  });
});

describe("identifier registry", () => {
  it("matches the schema in both documents and in this binding", () => {
    const nameDocument = schemas.get("ids/identifier-name.v1") as { enum: string[] };
    const identifiers = schemas.get("ids/identifiers.v1") as {
      properties: Record<string, JsonValue>;
    };
    expect(new Set(nameDocument.enum)).toEqual(new Set(Object.keys(identifiers.properties)));
    expect(new Set(nameDocument.enum)).toEqual(new Set<string>(IDENTIFIER_NAMES));
  });
});
