/**
 * The unit the exporter handles.
 *
 * A locally produced observation carries a typed payload; a record forwarded by
 * an instrumented child frame arrives as an already-normalized JSON body that
 * ingestion validates against the same schema. Both become an `ExportRecord`
 * before entering the buffer, so the export path has exactly one shape.
 */

import type { JsonObject } from "../core/json.js";
import type { Observation, ObservationEnvelope } from "../observation/types.js";

export interface ExportRecord {
  readonly envelope: ObservationEnvelope;
  readonly body: JsonObject;
}

export const toExportRecord = (observation: Observation): ExportRecord => ({
  envelope: observation.envelope,
  body: observation.payload.body,
});
