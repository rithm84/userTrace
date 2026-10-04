/**
 * The shipped configuration names control-plane base URLs that carry a path
 * prefix. Every request keeps that prefix, while possession-proof audiences
 * name the bare origin the control plane checks exactly.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { parseBuildOrigins } from "../../src/build/buildOrigins";

const CONFIG = resolve(import.meta.dirname, "../../config/build-origins.json");
const shipped: unknown = JSON.parse(readFileSync(CONFIG, "utf8"));
const origins = parseBuildOrigins(shipped);

describe("a control-plane base URL with a path", () => {
  it("keeps the prefix on every endpoint and names the bare origin as the proof audience", async () => {
    vi.stubGlobal("__UTRACE_BUILD_CONFIG__", shipped);
    vi.stubGlobal("__UTRACE_EXTENSION_VERSION__", "0.1.0");
    const config = await import("../../src/shared/buildConfig");

    expect(config.installationsUrl()).toBe(
      "https://api.utrace.dev/compute/v1/api/v1/extension/installations",
    );
    expect(config.controlSocketUrl()).toBe(
      "wss://api.utrace.dev/compute/v1/api/v1/extension/control",
    );
    expect(config.API_AUDIENCE).toBe("https://api.utrace.dev");
    expect(config.CONTROL_SOCKET_AUDIENCE).toBe("wss://api.utrace.dev");
  });

  it("drops a trailing slash and refuses a query", () => {
    expect(
      parseBuildOrigins({ ...origins, apiBaseUrl: "https://api.utrace.dev/compute/v1/api/" })
        .apiBaseUrl,
    ).toBe("https://api.utrace.dev/compute/v1/api");
    expect(() =>
      parseBuildOrigins({ ...origins, apiBaseUrl: "https://api.utrace.dev/compute/v1/api?x=1" }),
    ).toThrow(/no query, fragment, or credentials/u);
  });
});
