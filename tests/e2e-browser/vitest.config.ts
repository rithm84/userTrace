import { defineConfig } from "vitest/config";

/**
 * One file, run serially: the journey binds one extension installation to one
 * Interview Session, and a second concurrent run would compete for the same
 * ports, the same Chrome profile, and the same binding.
 */
export default defineConfig({
  test: {
    include: ["*.test.ts"],
    environment: "node",
    testTimeout: 180_000,
    hookTimeout: 600_000,
    teardownTimeout: 60_000,
    fileParallelism: false,
    pool: "forks",
  },
});
