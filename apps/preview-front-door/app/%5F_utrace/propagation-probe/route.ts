import { handlePropagationProbe } from "@/lib/relays";

/**
 * `POST /__utrace/propagation-probe` — the trace propagation probe the embedded
 * browser SDK requires before it reports readiness. The door echoes the
 * `traceparent` and `tracestate` it received, and how many `traceparent` values
 * arrived, so the SDK can prove one injector produced exactly one header.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = handlePropagationProbe;
