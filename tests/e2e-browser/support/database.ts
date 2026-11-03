/**
 * The journey's own Shopalytics database.
 *
 * Shopalytics needs a seeded Postgres, and the journey does not borrow the one a
 * developer's `.env.local` names: when that database is gone, every product
 * request fails and the journey stalls at the product sign-in. Instead the
 * client's own seed command installs the committed fixture into a schema the
 * journey owns, on the Postgres `E2E_DATABASE_URL` names. The seed recreates the
 * schema's tables, so every run starts from the same dataset.
 */

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export type ShopalyticsDatabase = Readonly<{
  url: string;
  schema: string;
  /** The committed fixture's clock, so the dashboard's presets select its rows. */
  fixtureClock: string;
}>;

const MANIFEST_PATH = "fixtures/dudulemon/manifest.json";

export function seedShopalyticsDatabase(
  input: Readonly<{ clientRoot: string; url: string; schema: string }>,
): ShopalyticsDatabase {
  const result = spawnSync("npm", ["run", "db:seed:base"], {
    cwd: input.clientRoot,
    env: {
      ...process.env,
      SHOPALYTICS_BASE_DATABASE_URL: input.url,
      SHOPALYTICS_DATABASE_SCHEMA: input.schema,
      DATABASE_SSL: "false",
    },
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `the Shopalytics fixture did not seed:\n${result.stdout}\n${result.stderr}`.slice(0, 8000),
    );
  }
  const manifest: unknown = JSON.parse(
    readFileSync(resolve(input.clientRoot, MANIFEST_PATH), "utf8"),
  );
  if (
    typeof manifest !== "object" ||
    manifest === null ||
    !("fixture_clock" in manifest) ||
    typeof manifest.fixture_clock !== "string"
  ) {
    throw new Error(`${MANIFEST_PATH} in ${input.clientRoot} has no fixture_clock`);
  }
  return { url: input.url, schema: input.schema, fixtureClock: manifest.fixture_clock };
}
