/**
 * Baseline chunking.
 *
 * A baseline larger than the per-observation limit is split into ordered,
 * individually hashed chunks. Ingestion makes it available only after the
 * terminal chunk and the aggregate hash validate, so a partially delivered
 * baseline is never treated as a complete one.
 */

import type { BaselineChunkPayload, BaselineSnapshotPayload } from "../observation/types.js";

/** Reserve for the envelope fields that share the per-observation budget. */
const ENVELOPE_RESERVE_BYTES = 2048;

const encoder = new TextEncoder();

const hashHex = async (subtle: SubtleCrypto, text: string): Promise<string> => {
  const digest = new Uint8Array(await subtle.digest("SHA-256", encoder.encode(text)));
  let hex = "";
  for (const byte of digest) hex += byte.toString(16).padStart(2, "0");
  return `sha256:${hex}`;
};

/**
 * Splits a serialized baseline into chunk payloads. Returns an empty array when
 * the baseline fits in one observation, in which case the caller sends the
 * snapshot record itself.
 */
export const chunkBaseline = async (
  baselineId: string,
  snapshot: BaselineSnapshotPayload,
  maxObservationBytes: number,
  subtle: SubtleCrypto,
): Promise<readonly BaselineChunkPayload[]> => {
  const serialized = JSON.stringify(snapshot);
  const budget = Math.max(1024, maxObservationBytes - ENVELOPE_RESERVE_BYTES);
  if (encoder.encode(serialized).byteLength <= budget) return [];

  const bodies: string[] = [];
  // Slice on code units and verify the encoded size, so a multi-byte character
  // cannot push a chunk past the budget.
  let start = 0;
  while (start < serialized.length) {
    let end = Math.min(serialized.length, start + budget);
    while (end > start && encoder.encode(serialized.slice(start, end)).byteLength > budget) {
      end -= Math.max(1, Math.floor((end - start) / 8));
    }
    bodies.push(serialized.slice(start, end));
    start = end;
  }

  const hashes = await Promise.all(bodies.map(async (body) => hashHex(subtle, body)));
  const aggregate = await hashHex(subtle, hashes.join("|"));
  return bodies.map((body, index) => ({
    baseline_id: baselineId,
    chunk_index: index,
    chunk_count: bodies.length,
    chunk_hash: hashes[index] ?? "",
    aggregate_hash: index === bodies.length - 1 ? aggregate : undefined,
    chunk_body: body,
  }));
};
