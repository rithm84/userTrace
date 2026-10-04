/**
 * Fixture, schema, and binding discovery shared by the conformance tests.
 *
 * Discovery is by directory layout, not registration: any
 * `fixtures/<area>/valid` or `fixtures/<area>/invalid` directory is picked up,
 * each file's name up to `__` names the contract it exercises, and every
 * `src/<area>/contracts.ts` that exports `areaContracts` publishes that area's
 * bindings. An area workstream adds files, never a line in a list here.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import type { JsonValue } from "../src/json.js";
import { FIXTURES_ROOT, SCHEMAS_ROOT } from "../src/locations.js";
import { AREA_CONTRACTS_EXPORT, type ContractBindings } from "../src/registry.js";

export const CONTRACT_SEPARATOR = "__";
export const NON_AREA_PREFIX = "_";
const SCHEMA_SUFFIX = ".schema.json";

export interface Fixture {
  readonly area: string;
  readonly contract: string;
  readonly case: string;
  readonly path: string;
  readonly expectValid: boolean;
  readonly label: string;
}

function readJson(path: string): JsonValue {
  return JSON.parse(readFileSync(path, "utf8")) as JsonValue;
}

export function loadInstance(fixture: Fixture): JsonValue {
  return readJson(fixture.path);
}

function directories(root: string): string[] {
  return readdirSync(root).filter((name) => statSync(join(root, name)).isDirectory());
}

function readBucket(areaRoot: string, area: string, bucket: string, expectValid: boolean): Fixture[] {
  const bucketRoot = join(areaRoot, bucket);
  let names: string[];
  try {
    names = readdirSync(bucketRoot);
  } catch {
    return [];
  }
  return names
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => {
      const stem = name.slice(0, -".json".length);
      const separator = stem.indexOf(CONTRACT_SEPARATOR);
      if (separator < 0) {
        throw new Error(
          `${join(bucketRoot, name)} must be named <name>.v<major>${CONTRACT_SEPARATOR}<case>.json ` +
            "so the conformance suite can tell which schema it exercises",
        );
      }
      const contract = stem.slice(0, separator);
      return {
        area,
        contract,
        case: stem.slice(separator + CONTRACT_SEPARATOR.length),
        path: join(bucketRoot, name),
        expectValid,
        label: `${area}/${bucket}/${stem}`,
      };
    });
}

/** Every fixture under `fixtures/<area>/{valid,invalid}`. */
export function discoverFixtures(): Fixture[] {
  const fixtures: Fixture[] = [];
  for (const area of directories(FIXTURES_ROOT).sort()) {
    if (area.startsWith(NON_AREA_PREFIX)) {
      continue;
    }
    const areaRoot = join(FIXTURES_ROOT, area);
    const buckets = directories(areaRoot);
    if (!buckets.includes("valid") && !buckets.includes("invalid")) {
      throw new Error(
        `${areaRoot} is a fixture area but carries neither a valid/ nor an invalid/ directory; ` +
          `prefix the directory with '${NON_AREA_PREFIX}' if it is not a contract area`,
      );
    }
    fixtures.push(...readBucket(areaRoot, area, "valid", true));
    fixtures.push(...readBucket(areaRoot, area, "invalid", false));
  }
  if (fixtures.length === 0) {
    throw new Error(`no fixtures discovered under ${FIXTURES_ROOT}`);
  }
  return fixtures;
}

/** Every schema document on disk, keyed by `"<area>/<name>.v<major>"`. */
export function discoverSchemas(): Map<string, JsonValue> {
  const schemas = new Map<string, JsonValue>();
  for (const area of directories(SCHEMAS_ROOT).sort()) {
    for (const name of readdirSync(join(SCHEMAS_ROOT, area)).sort()) {
      if (!name.endsWith(SCHEMA_SUFFIX)) {
        continue;
      }
      const contract = name.slice(0, -SCHEMA_SUFFIX.length);
      schemas.set(`${area}/${contract}`, readJson(join(SCHEMAS_ROOT, area, name)));
    }
  }
  return schemas;
}

const areaModules = import.meta.glob<Record<string, unknown>>("../src/*/contracts.ts", {
  eager: true,
});

/** Every area's bindings, keyed by area then `"<name>.v<major>"`. */
export function discoverBindings(): Map<string, ContractBindings> {
  const bindings = new Map<string, ContractBindings>();
  for (const [path, module] of Object.entries(areaModules)) {
    const area = path.split("/").at(-2);
    if (area === undefined) {
      throw new Error(`cannot tell which area ${path} belongs to`);
    }
    const exported = module[AREA_CONTRACTS_EXPORT];
    if (exported === undefined) {
      throw new Error(
        `${path} exists but does not export ${AREA_CONTRACTS_EXPORT}; every contract area ` +
          "must declare its bindings there",
      );
    }
    bindings.set(area, exported as ContractBindings);
  }
  return bindings;
}
