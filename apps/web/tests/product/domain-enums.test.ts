import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { DOMAIN_ENUMS } from "@/lib/api/schemas/lifecycle";

/**
 * Reads the shipped schema files directly rather than through `@utrace/contracts`:
 * importing the package pulls in the `envelope` binding's own dependencies, which
 * the web application does not have and does not need.
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
});
