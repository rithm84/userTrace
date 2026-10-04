import { registerOTel } from "@vercel/otel";

/**
 * Next.js framework and outbound fetch spans for Vercel tracing. Trace context
 * propagates only to Vercel deployment URLs (the `@vercel/otel` default), so
 * calls to the control plane carry no added `traceparent`.
 */
export function register(): void {
  registerOTel({ serviceName: "utrace-web" });
}
