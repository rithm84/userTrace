import { defineConfig } from "vitest/config";

/**
 * Two projects: `unit` runs the pure protocol, state, and geometry tests;
 * `chrome` drives the packaged build in a real Chrome and needs the longer
 * budget of a browser launch. `npm test` runs both, building `dist-e2e` first.
 */
export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: "unit",
          include: ["tests/unit/**/*.test.ts"],
          environment: "node",
        },
      },
      {
        test: {
          name: "chrome",
          include: ["tests/chrome/**/*.test.ts"],
          environment: "node",
          testTimeout: 120_000,
          hookTimeout: 120_000,
          fileParallelism: false,
        },
      },
    ],
  },
});
