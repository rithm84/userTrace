/**
 * Extension diagnostics as one readable line.
 *
 * Chrome's extension Errors page renders a logged object as `[object Object]`,
 * which hides the cause, so every entry is a single string: the event name
 * followed by its fields serialized as JSON. Fields carry identifiers, reason
 * codes, revisions, and the extension's own error messages; callers never pass
 * page content, form values, transcripts, or credentials.
 */
export type LogValue = string | number | boolean | null;
export type LogFields = Readonly<Record<string, LogValue>>;

function formatLogEntry(event: string, fields: LogFields): string {
  return `${event} ${JSON.stringify(fields)}`;
}

export function logWarning(event: string, fields: LogFields): void {
  console.warn(formatLogEntry(event, fields));
}

export function logError(event: string, fields: LogFields): void {
  console.error(formatLogEntry(event, fields));
}

/** The name, reason code where the error carries one, and message of a failure. */
export function failureFields(error: unknown): LogFields {
  if (!(error instanceof Error)) {
    return { name: "unknown", message: String(error) };
  }
  const code = "code" in error && typeof error.code === "string" ? { code: error.code } : {};
  return { name: error.name, ...code, message: error.message };
}
