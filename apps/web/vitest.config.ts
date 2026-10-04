import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    preserveSymlinks: true,
    alias: {
      "@utrace/agent-character": path.resolve(
        __dirname,
        "tests/mocks/agent-character.tsx",
      ),
      // Server modules are exercised directly in jsdom; `next build` keeps the real guard.
      "server-only": path.resolve(__dirname, "tests/mocks/server-only.ts"),
      "@": path.resolve(__dirname),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
