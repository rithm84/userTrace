/**
 * The `browser-observation` schemas compile under ajv's strict mode, including
 * strict types. Fixture conformance for this area runs in
 * `packages/utrace-contracts/typescript/tests/conformance.test.ts`.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { SCHEMAS_DIR, schemaId } from "./helpers/contracts.js";

const ajv = new Ajv2020({ strict: true, allErrors: true, allowUnionTypes: true });
addFormats(ajv);

const schemaFiles = readdirSync(SCHEMAS_DIR).filter((name) => name.endsWith(".schema.json"));
for (const file of schemaFiles) {
  ajv.addSchema(JSON.parse(readFileSync(join(SCHEMAS_DIR, file), "utf8")) as object);
}

describe("browser-observation contract area", () => {
  it("compiles every schema", () => {
    for (const file of schemaFiles) {
      expect(() => ajv.getSchema(schemaId(file.replace(".schema.json", "")))).not.toThrow();
    }
  });
});
