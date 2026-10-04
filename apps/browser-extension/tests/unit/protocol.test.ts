/**
 * Conformance between the JSON Schemas, the shared fixtures, and the zod
 * bindings the extension actually runs.
 *
 * Every canonical instance must be accepted by both validators and every
 * invalid instance rejected by both, so a binding cannot drift away from the
 * contract without failing here.
 */
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { Ajv2020 } from "ajv/dist/2020.js";
import type { AnySchemaObject, ValidateFunction } from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";

import { RFC3339_UTC_PATTERN, UUID_V7_PATTERN } from "../../src/protocol/common";
import { parseInboundFrame, parseOutboundFrame } from "../../src/protocol/control";
import { parseBridgeFrame } from "../../src/protocol/bridge";
import { installationMessageSchema } from "../../src/protocol/installation";

const CONTRACTS_ROOT = resolve(import.meta.dirname, "../../../../packages/utrace-contracts");
const SCHEMA_DIR = resolve(CONTRACTS_ROOT, "schemas/extension-protocol");
const FIXTURE_DIR = resolve(CONTRACTS_ROOT, "fixtures/extension-protocol");

type Surface = Readonly<{
  directory: string;
  schemaId: string;
  parse: (candidate: unknown) => unknown;
}>;

const SURFACES: readonly Surface[] = [
  {
    directory: "inbound",
    schemaId: "https://utrace.dev/schemas/extension-protocol/inbound-frame/v1",
    parse: parseInboundFrame,
  },
  {
    directory: "outbound",
    schemaId: "https://utrace.dev/schemas/extension-protocol/outbound-frame/v1",
    parse: parseOutboundFrame,
  },
  {
    directory: "bridge",
    schemaId: "https://utrace.dev/schemas/extension-protocol/bridge-frame/v1",
    parse: parseBridgeFrame,
  },
  {
    directory: "installation",
    schemaId: "https://utrace.dev/schemas/extension-protocol/installation-registration/v1",
    parse: (candidate: unknown): unknown => installationMessageSchema.parse(candidate),
  },
];

function createValidator(): (schemaId: string) => ValidateFunction {
  // `strictRequired` is off because the conditional branches require properties
  // declared by their parent schema, which is exactly how the contract reads.
  const ajv = new Ajv2020({
    strict: true,
    strictRequired: false,
    allErrors: true,
    formats: { uuid: UUID_V7_PATTERN, "date-time": RFC3339_UTC_PATTERN },
  });

  for (const file of readdirSync(SCHEMA_DIR).filter((name) => name.endsWith(".schema.json"))) {
    ajv.addSchema(readSchema(resolve(SCHEMA_DIR, file)));
  }

  return (schemaId: string): ValidateFunction => {
    const validate = ajv.getSchema(schemaId);
    if (validate === undefined) {
      throw new Error(`No compiled schema for ${schemaId}.`);
    }
    return validate;
  };
}

const getValidator = createValidator();

function readSchema(path: string): AnySchemaObject {
  return JSON.parse(readFileSync(path, "utf8")) as AnySchemaObject;
}

/**
 * Which surface each message document belongs to, read from the frame unions.
 *
 * Fixtures are named `<schema-name>.v<major>__<case>.json` in one flat
 * directory per bucket, the way every contract area names them, so the surface
 * comes from the union that references the document rather than from a
 * directory the fixture happens to sit in.
 */
function surfaceOfContract(): ReadonlyMap<string, string> {
  const surfaces = new Map<string, string>();
  for (const surface of SURFACES) {
    const union = surface.schemaId.split("/").slice(-2, -1)[0] ?? "";
    surfaces.set(union, surface.directory);
    if (surface.directory === "installation") {
      continue;
    }
    const schema = readSchema(resolve(SCHEMA_DIR, `${union}.v1.schema.json`));
    for (const branch of schema["oneOf"] as readonly { $ref: string }[]) {
      surfaces.set(branch.$ref.split("/").slice(-2, -1)[0] ?? "", surface.directory);
    }
  }
  return surfaces;
}

const SURFACE_OF_CONTRACT = surfaceOfContract();

function readFixtures(kind: "valid" | "invalid", directory: string): readonly [string, unknown][] {
  const path = resolve(FIXTURE_DIR, kind);
  return readdirSync(path)
    .filter((name) => name.endsWith(".json"))
    .filter((name) => {
      const contract = name.slice(0, name.indexOf("__")).replace(".v1", "");
      const surface = SURFACE_OF_CONTRACT.get(contract);
      if (surface === undefined) {
        throw new Error(`${name} names ${contract}, which no frame union references`);
      }
      return surface === directory;
    })
    .map((name) => [name, JSON.parse(readFileSync(resolve(path, name), "utf8"))] as const);
}

describe.each(SURFACES)("$directory fixtures", (surface) => {
  const validate = getValidator(surface.schemaId);

  it.each(readFixtures("valid", surface.directory))(
    "accepts %s in both validators",
    (_name, fixture) => {
      expect(validate(fixture), JSON.stringify(validate.errors)).toBe(true);
      expect(() => surface.parse(fixture)).not.toThrow();
    },
  );

  it.each(readFixtures("invalid", surface.directory))(
    "rejects %s in both validators",
    (_name, fixture) => {
      expect(validate(fixture)).toBe(false);
      expect(() => surface.parse(fixture)).toThrow();
    },
  );
});

describe("schema documents", () => {
  it("names every message document in a frame union or the registration document", () => {
    const documents = readdirSync(SCHEMA_DIR)
      .filter((name) => name.endsWith(".schema.json"))
      .map((name) => name.replace(".v1.schema.json", ""));
    const unions = ["inbound-frame", "outbound-frame", "bridge-frame"];
    const referenced = new Set<string>(["common", "scene", "installation-registration", ...unions]);

    for (const union of unions) {
      const schema = readSchema(resolve(SCHEMA_DIR, `${union}.v1.schema.json`));
      const branches = schema["oneOf"] as readonly { $ref: string }[];
      for (const branch of branches) {
        referenced.add(branch.$ref.split("/").slice(-2, -1)[0] ?? "");
      }
    }

    expect([...documents].sort()).toStrictEqual([...referenced].sort());
  });
});
