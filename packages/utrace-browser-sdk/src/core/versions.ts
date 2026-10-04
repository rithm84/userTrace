/** Protocol, schema, and policy versions this build of the SDK speaks. */

/** Browser observation schema major understood by this build. */
export const SUPPORTED_SCHEMA_VERSION = "1";

/** Browser activation and ingestion protocol major understood by this build. */
export const SUPPORTED_PROTOCOL_VERSION = "1";

/**
 * Schema major for frames on the extension bridge port. It is a number, not a
 * string, because the extension protocol's `bridge-frame.v1` schema is the one
 * owner of that wire format and encodes it as an integer.
 */
export const BRIDGE_SCHEMA_VERSION = 1;

/** Pinned OpenTelemetry semantic convention schema for this browser stream. */
export const SEMANTIC_CONVENTION_SCHEMA_URL = "https://opentelemetry.io/schemas/1.38.0";

/** Versioned uTrace attribute namespace. Never an alias for a standard attribute. */
export const UTRACE_ATTRIBUTE_NAMESPACE = "utrace.v1";

/** WebSocket subprotocol advertised to the browser telemetry ingestion edge. */
export const INGEST_SUBPROTOCOL = "utrace.browser.v1";

/** Prefix for the credential-bearing subprotocol value. */
export const INGEST_CREDENTIAL_SUBPROTOCOL_PREFIX = "utrace.credential.";

/** Reserved same-origin paths served by the preview front door. */
export const ACTIVATE_PATH = "/__utrace/activate";
export const TELEMETRY_CREDENTIAL_PATH = "/__utrace/telemetry-credential";
export const PROPAGATION_PROBE_PATH = "/__utrace/propagation-probe";
