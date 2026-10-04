/**
 * Trace propagation probe.
 *
 * Readiness requires a successful trace propagation probe without duplicate
 * injectors. The probe issues one same-origin request through the installed
 * fetch hook and asks the preview front door to echo exactly what it received.
 * Two `traceparent` values mean a second injector is active, which is a
 * `trace_correlation_failure` rather than a tolerable duplicate.
 */

import type { BrowserView } from "../core/dom.js";
import { UTraceRuntimeError } from "../core/failure.js";
import { asObject, parseJson, requireInteger, requireString } from "../core/json.js";
import { PROPAGATION_PROBE_PATH } from "../core/versions.js";
import { UTRACE_TRACESTATE } from "../trace/context.js";
import type { TraceCoordinator } from "../trace/context.js";

const TRACEPARENT = /^00-([0-9a-f]{32})-([0-9a-f]{16})-0[0-9a-f]$/;

export interface ProbeResult {
  readonly trace_id: string;
  readonly span_id: string;
}

/**
 * Runs the probe. The caller supplies the document's fetch, which must already
 * be the hooked one, so the probe exercises the real injection path.
 */
export const runPropagationProbe = async (
  view: BrowserView,
  trace: TraceCoordinator,
  fetchImpl: typeof fetch,
): Promise<ProbeResult> => {
  const expected = trace.startInteractionSpan();
  let response: Response;
  try {
    response = await fetchImpl(new URL(PROPAGATION_PROBE_PATH, view.location.origin).toString(), {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ probe: "trace_propagation" }),
    });
  } catch (cause) {
    trace.endInteractionSpan();
    throw new UTraceRuntimeError("trace_correlation_failure", "the propagation probe request failed", {
      path: PROPAGATION_PROBE_PATH,
      cause: String(cause),
    });
  }
  const text = await response.text();
  trace.endInteractionSpan();
  if (!response.ok) {
    throw new UTraceRuntimeError("trace_correlation_failure", "the propagation probe returned a non-success status", {
      path: PROPAGATION_PROBE_PATH,
      status: response.status,
      body: text.slice(0, 256),
    });
  }
  const body = asObject(parseJson(text, "propagation_probe"), "propagation_probe");
  const count = requireInteger(body, "traceparent_count", "propagation_probe");
  if (count !== 1) {
    throw new UTraceRuntimeError("trace_correlation_failure", "the probe request carried duplicate injectors", {
      traceparent_count: count,
    });
  }
  const traceparent = requireString(body, "traceparent", "propagation_probe");
  const match = TRACEPARENT.exec(traceparent);
  if (match === null) {
    throw new UTraceRuntimeError("trace_correlation_failure", "the probe echoed a malformed traceparent", {
      received_length: traceparent.length,
    });
  }
  const traceId = match[1] ?? "";
  const spanId = match[2] ?? "";
  if (traceId !== expected.trace_id) {
    throw new UTraceRuntimeError("trace_correlation_failure", "the probe span did not join the interaction trace", {
      expected_trace_id: expected.trace_id,
      received_trace_id: traceId,
    });
  }
  const tracestate = requireString(body, "tracestate", "propagation_probe");
  if (!tracestate.includes(UTRACE_TRACESTATE)) {
    throw new UTraceRuntimeError("trace_correlation_failure", "the probe echoed no uTrace tracestate entry", {
      tracestate,
    });
  }
  return { trace_id: traceId, span_id: spanId };
};
