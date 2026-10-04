import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy } from "@/lib/csp";

describe("landing Content-Security-Policy", () => {
  it("supports self-hosted Rive without product API or auth configuration", () => {
    const csp = buildContentSecurityPolicy({ NODE_ENV: "production" });

    expect(csp).toContain("connect-src 'self';");
    expect(csp).toContain("script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'");
    expect(csp).toContain("worker-src 'self' blob:");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toContain("localhost");
  });

  it("allows development hot reload and WebAssembly", () => {
    const csp = buildContentSecurityPolicy({ NODE_ENV: "development" });

    expect(csp).toContain("'wasm-unsafe-eval' 'unsafe-eval'");
    expect(csp).toContain("ws://localhost:*");
    expect(csp).toContain("ws://127.0.0.1:*");
  });
});
