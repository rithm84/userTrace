import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    // Every suite binds real ports in the 8300-8399 range and installs one
    // process-wide OpenTelemetry provider, so suites must not share a worker.
    pool: 'forks',
    fileParallelism: false,
    maxWorkers: 1,
    testTimeout: 60_000,
    hookTimeout: 60_000,
  },
});
