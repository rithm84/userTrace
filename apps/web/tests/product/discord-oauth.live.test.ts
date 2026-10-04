import { describe, expect, it } from "vitest";

/**
 * A live check that the Supabase project really has the Discord provider
 * enabled, which is dashboard configuration rather than repository code.
 *
 * It is off unless `SUPABASE_DISCORD_OAUTH_ENABLED=1`, because enabling the
 * provider needs a Discord client secret this repository does not hold. Run it
 * with the project's settings exported, for example:
 *
 *   set -a; source ../../.env.local; set +a
 *   SUPABASE_DISCORD_OAUTH_ENABLED=1 npx vitest run tests/product/discord-oauth.live.test.ts
 */

const enabled = process.env.SUPABASE_DISCORD_OAUTH_ENABLED?.trim() === "1";

describe.skipIf(!enabled)("Discord sign-in provider", () => {
  it("is enabled on the Supabase project", async () => {
    const url = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
    if (url === undefined || key === undefined) {
      throw new Error(
        "SUPABASE_DISCORD_OAUTH_ENABLED=1 needs SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY exported.",
      );
    }

    const response = await fetch(`${url.replace(/\/+$/u, "")}/auth/v1/settings`, {
      headers: { apikey: key, authorization: `Bearer ${key}` },
    });
    expect(response.ok).toBe(true);

    const settings = (await response.json()) as { external?: Record<string, boolean> };
    expect(settings.external?.discord).toBe(true);
  });
});
