import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, ".") },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    globals: false,
    // The door reads configuration from the process environment and keeps a per-instance
    // route-revision guard, so suites run one file at a time.
    fileParallelism: false,
  },
});
