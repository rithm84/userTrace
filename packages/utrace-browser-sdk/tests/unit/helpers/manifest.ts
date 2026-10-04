/** Builds the activation-pinned manifest index used by the unit suites. */

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { decodeAnnotationManifest, ManifestIndex } from "../../../src/annotations/manifest.js";
import type { JsonObject } from "../../../src/core/json.js";

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(here, "../../chrome/fixture/utrace.annotations.json");

export const testManifestPayload = (): JsonObject => ({
  manifest_version: "test-manifest-1",
  client_release_id: "shopalytics-main-test",
  ...(JSON.parse(readFileSync(FIXTURE, "utf8")) as JsonObject),
});

export const testManifestIndex = (): ManifestIndex => new ManifestIndex(decodeAnnotationManifest(testManifestPayload()));

export const FIXTURE_PATH = join(dirname(FIXTURE), "utrace.annotations.json");
