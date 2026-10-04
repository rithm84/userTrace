/**
 * Conformance over the `browser-observation` contract area.
 *
 * Every schema must compile, every `valid` fixture must be accepted, and every
 * `invalid` fixture must be rejected. Fixture files are named
 * `<schema-name>.<case>.json`, so the schema under test comes from the filename
 * and no separate index has to be kept in step.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { CONTRACTS_DIR, FIXTURES_DIR, SCHEMAS_DIR, schemaId } from "./helpers/contracts.js";

const ajv = new Ajv2020({ strict: true, allErrors: true, allowUnionTypes: true });
addFormats(ajv);

const schemaFiles = readdirSync(SCHEMAS_DIR).filter((name) => name.endsWith(".schema.json"));
for (const file of schemaFiles) {
  ajv.addSchema(JSON.parse(readFileSync(join(SCHEMAS_DIR, file), "utf8")) as object);
}

const fixtures = (kind: "valid" | "invalid"): readonly string[] =>
  readdirSync(join(FIXTURES_DIR, kind)).filter((name) => name.endsWith(".json"));

describe("browser-observation contract area", () => {
  it("lives beside the other contract areas", () => {
    expect(readdirSync(CONTRACTS_DIR)).toContain("schemas");
  });

  it("compiles every schema", () => {
    for (const file of schemaFiles) {
      expect(() => ajv.getSchema(schemaId(file.replace(".schema.json", "")))).not.toThrow();
    }
  });

  it.each(fixtures("valid"))("accepts the canonical instance %s", (name) => {
    const validate = ajv.getSchema(schemaId(`${name.split(".")[0] ?? ""}.v1`));
    expect(validate, `no schema for fixture ${name}`).toBeDefined();
    const instance: unknown = JSON.parse(readFileSync(join(FIXTURES_DIR, "valid", name), "utf8"));
    const ok = validate?.(instance) ?? false;
    expect(ok, JSON.stringify(validate?.errors ?? [], null, 2)).toBe(true);
  });

  it.each(fixtures("invalid"))("rejects %s", (name) => {
    const validate = ajv.getSchema(schemaId(`${name.split(".")[0] ?? ""}.v1`));
    expect(validate, `no schema for fixture ${name}`).toBeDefined();
    const instance: unknown = JSON.parse(readFileSync(join(FIXTURES_DIR, "invalid", name), "utf8"));
    expect(validate?.(instance) ?? true).toBe(false);
  });
});
