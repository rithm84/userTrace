import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: "unit",
          environment: "jsdom",
          include: ["tests/unit/**/*.test.ts"],
        },
      },
      {
        test: {
          name: "chrome",
          environment: "node",
          include: ["tests/chrome/**/*.test.ts"],
          testTimeout: 120_000,
          hookTimeout: 120_000,
          fileParallelism: false,
        },
      },
    ],
  },
});
