/**
 * Fixed platform ceilings.
 *
 * The activation handshake supplies operational limits; they may only be
 * tightened relative to these ceilings, never raised.
 */
export const MAX_OBSERVATION_BYTES = 256 * 1024;
export const MAX_BATCH_BYTES = 1024 * 1024;

/** Bounded memory-only buffer. Nothing is written to any browser storage. */
export const MAX_BUFFERED_RECORDS = 4096;
export const MAX_BUFFERED_BYTES = 8 * 1024 * 1024;

/** Coalescing windows, in milliseconds. */
export const DOM_COALESCE_WINDOW_MS = 250;
export const PERFORMANCE_BATCH_WINDOW_MS = 5000;
export const GEOMETRY_PUBLISH_WINDOW_MS = 100;

/** Bounded flush budget for `pagehide`. */
export const PAGEHIDE_FLUSH_BYTES = 64 * 1024;

/** Cardinality and length bounds applied by redaction step seven. */
export const MAX_SAFE_VALUE_LENGTH = 120;
export const MAX_LABEL_LENGTH = 120;
export const MAX_BASELINE_NODES = 1500;
export const MAX_DOM_CHANGES_PER_RECORD = 200;

/** Detail-query bounds. A query may only request less than these. */
export const MAX_DETAIL_QUERY_FIELDS = 24;
export const MAX_DETAIL_QUERY_DEPTH = 4;
export const MAX_DETAIL_QUERY_BYTES = 32 * 1024;

/** Clamp a negotiated limit to its platform ceiling. */
export const clampLimit = (negotiated: number, ceiling: number): number =>
  negotiated > 0 && negotiated < ceiling ? negotiated : ceiling;
