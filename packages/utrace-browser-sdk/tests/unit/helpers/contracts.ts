/** Locations of the `browser-observation` contract area this package owns. */

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export const SCHEMAS_DIR = resolve(here, "../../../../utrace-contracts/schemas/browser-observation");

export const schemaId = (name: string): string => `https://utrace.dev/schemas/browser-observation/${name.replace(".v1", "")}/v1`;
