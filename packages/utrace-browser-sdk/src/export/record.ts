/**
 * The unit the exporter handles.
 *
 * A locally produced observation carries a typed payload; a record forwarded by
 * an instrumented child frame arrives as an already-normalized JSON body that
 * ingestion validates against the same schema. Both become an `ExportRecord`
 * before entering the buffer, so the export path has exactly one shape.
 */

import type { JsonObject } from "../core/json.js";
import type { ObservationEnvelope } from "../observation/types.js";

export interface ExportRecord {
  readonly envelope: ObservationEnvelope;
  readonly body: JsonObject;
}
