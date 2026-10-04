import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020";
import type { SchemaObject } from "ajv";
import addFormats from "ajv-formats";
import type { ZodType } from "zod";
import { describe, expect, it } from "vitest";

import {
  activationRelayRequestSchema,
  activationRequestSchema,
  activationSchema,
  handoffExchangeRequestSchema,
  handoffExchangeSchema,
  previewDenialSchema,
  previewRouteRecordSchema,
  productSignInSchema,
  relayFailureSchema,
  routeContextClaimsSchema,
  telemetryCredentialRelayRequestSchema,
  telemetryCredentialRequestSchema,
  telemetryCredentialSchema,
} from "@/lib/contracts";

/**
 * Conformance between the shared JSON Schemas, their fixtures, and the door's own zod
 * mirrors. Every canonical fixture must be accepted by both, and every fixture named for
 * a violated rule must be rejected by both, so the two representations cannot drift.
 */

const contractsRoot = path.resolve(
  import.meta.dirname,
  "../../../packages/utrace-contracts",
);
const schemaDirectory = path.join(contractsRoot, "schemas/preview");
const fixtureDirectory = path.join(contractsRoot, "fixtures/preview");

const zodMirrors: Readonly<Record<string, ZodType>> = {
  activation: activationSchema,
  "activation-relay-request": activationRelayRequestSchema,
  "activation-request": activationRequestSchema,
  denial: previewDenialSchema,
  "handoff-exchange": handoffExchangeSchema,
  "handoff-exchange-request": handoffExchangeRequestSchema,
  "product-sign-in": productSignInSchema,
  "relay-failure": relayFailureSchema,
  "route-context-token": routeContextClaimsSchema,
  "route-record": previewRouteRecordSchema,
  "telemetry-credential": telemetryCredentialSchema,
  "telemetry-credential-relay-request": telemetryCredentialRelayRequestSchema,
  "telemetry-credential-request": telemetryCredentialRequestSchema,
};

const ajv = addFormats(new Ajv2020({ strict: true, allErrors: true }));

const schemaNames = readdirSync(schemaDirectory)
  .filter((name) => name.endsWith(".v1.schema.json"))
  .map((name) => name.replace(".v1.schema.json", ""));

const readJson = (file: string): unknown =>
  JSON.parse(readFileSync(file, "utf8"));

const readSchema = (file: string): SchemaObject => {
  const parsed = readJson(file);
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error(`schema ${file} is not a JSON object`);
  }
  return parsed as SchemaObject;
};

// Every schema is registered before any is compiled, because the relay
// requests wrap the browser's own request document by `$ref`.
for (const name of schemaNames) {
  ajv.addSchema(readSchema(path.join(schemaDirectory, `${name}.v1.schema.json`)));
}

const validators = new Map(
  schemaNames.map((name) => {
    const id = `https://utrace.dev/schemas/preview/${name}/v1`;
    const validate = ajv.getSchema(id);
    if (validate === undefined) {
      throw new Error(`no compiled validator for ${id}`);
    }
    return [name, validate] as const;
  }),
);

const fixturesIn = (
  kind: "valid" | "invalid",
): readonly { name: string; schemaName: string; body: unknown }[] =>
  readdirSync(path.join(fixtureDirectory, kind))
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      const schemaName = schemaNames
        .filter((candidate) => name.startsWith(`${candidate}.`))
        .sort((left, right) => right.length - left.length)[0];
      if (schemaName === undefined) {
        throw new Error(`fixture ${name} does not name a preview schema`);
      }
      return {
        name,
        schemaName,
        body: readJson(path.join(fixtureDirectory, kind, name)),
      };
    });

describe("preview contract schemas", () => {
  it("publishes a zod mirror and a compiled validator for every JSON Schema", () => {
    expect(schemaNames.toSorted()).toStrictEqual(
      Object.keys(zodMirrors).toSorted(),
    );
    expect(validators.size).toBe(schemaNames.length);
  });

  it.each(fixturesIn("valid").map((fixture) => [fixture.name, fixture] as const))(
    "accepts the canonical fixture %s in both representations",
    (_name, fixture) => {
      expect(validators.get(fixture.schemaName)?.(fixture.body)).toBe(true);
      expect(
        zodMirrors[fixture.schemaName]?.safeParse(fixture.body).success,
      ).toBe(true);
    },
  );

  it.each(
    fixturesIn("invalid").map((fixture) => [fixture.name, fixture] as const),
  )("rejects %s in both representations", (_name, fixture) => {
    expect(validators.get(fixture.schemaName)?.(fixture.body)).toBe(false);
    expect(
      zodMirrors[fixture.schemaName]?.safeParse(fixture.body).success,
    ).toBe(false);
  });

  it("covers every schema with at least one canonical fixture", () => {
    const covered = new Set(
      fixturesIn("valid").map((fixture) => fixture.schemaName),
    );
    expect([...covered].toSorted()).toStrictEqual(schemaNames.toSorted());
  });
});
