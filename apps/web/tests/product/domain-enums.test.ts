import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { DOMAIN_ENUMS } from "@/lib/api/schemas/lifecycle";

/**
 * The shared JSON Schemas are the source of truth for the domain vocabulary.
 *
 * `@utrace/contracts` publishes TypeScript bindings for `envelope` and `ids`
 * only; the `domain` area's bindings live with its consumers, so the web holds
 * its own copy of these enumerations. This test is what keeps that copy honest:
 * it reads the shipped schema for every one of them and requires the same
 * members in the same order. A value the control plane adds or removes fails
 * here rather than at a page.
 *
 * It reads the schema files directly rather than through the package, because
 * importing the package pulls in the `envelope` binding's own dependencies,
 * which the web application does not have and does not need.
 */
const DOMAIN_SCHEMAS = join(
  process.cwd(),
  "..",
  "..",
  "packages",
  "utrace-contracts",
  "schemas",
  "domain",
);

function schemaEnum(name: string): readonly string[] {
  const document: unknown = JSON.parse(
    readFileSync(join(DOMAIN_SCHEMAS, `${name}.v1.schema.json`), "utf8"),
  );
  if (
    typeof document !== "object" ||
    document === null ||
    !("enum" in document) ||
    !Array.isArray(document.enum)
  ) {
    throw new Error(`${name}.v1.schema.json declares no enum`);
  }
  return document.enum.map((value) => String(value));
}

describe("domain lifecycle vocabulary", () => {
  it.each(Object.keys(DOMAIN_ENUMS))("%s matches the shipped contract schema", (name) => {
    expect(DOMAIN_ENUMS[name]).toEqual(schemaEnum(name));
  });

  it("covers every domain enumeration the web reads from the control plane", () => {
    // A bare count guards against a schema being dropped from the map by hand.
    expect(Object.keys(DOMAIN_ENUMS)).toHaveLength(21);
  });
});
