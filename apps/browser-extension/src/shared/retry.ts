/**
 * Bounded retries for calls that leave the extension.
 *
 * Each failed attempt logs a structured warning; the last error is rethrown
 * unchanged so the caller fails with the real cause rather than a summary.
 */
import { logWarning } from "./log";

export type RetryPolicy = Readonly<{
  attempts: number;
  initialDelayMs: number;
  operation: string;
}>;

export async function withRetries<Result>(
  policy: RetryPolicy,
  call: () => Promise<Result>,
): Promise<Result> {
  if (policy.attempts < 1) {
    throw new RangeError(`A retry policy needs at least one attempt; received ${String(policy.attempts)}.`);
  }

  let lastError: unknown = null;
  for (let attempt = 1; attempt <= policy.attempts; attempt += 1) {
    try {
      return await call();
    } catch (error) {
      lastError = error;
      logWarning("utrace.extension.retry", {
        operation: policy.operation,
        attempt,
        attempts: policy.attempts,
        message: error instanceof Error ? error.message : String(error),
      });
      if (attempt < policy.attempts) {
        await delay(policy.initialDelayMs * 2 ** (attempt - 1));
      }
    }
  }
  throw lastError;
}

export function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}
