/** Locations of the `browser-observation` contract area this package owns. */

import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export const CONTRACTS_DIR = resolve(here, "../../../../utrace-contracts");
export const SCHEMAS_DIR = join(CONTRACTS_DIR, "schemas", "browser-observation");
export const FIXTURES_DIR = join(CONTRACTS_DIR, "fixtures", "browser-observation");

export const schemaId = (name: string): string => `https://utrace.dev/schemas/browser-observation/${name.replace(".v1", "")}/v1`;
