import { z } from "zod";

export type SearchParams = Readonly<Record<string, string | string[] | undefined>>;

/** Only in-application paths survive, so a crafted link cannot redirect off site. */
const nextPathSchema = z.string().regex(/^\/(?!\/)[A-Za-z0-9\-._~!$&'()*+,;=:@/%]*$/u);

export function readNextPath(params: SearchParams, whenAbsent: string): string {
  const parsed = nextPathSchema.safeParse(params.next);
  return parsed.success ? parsed.data : whenAbsent;
}

/** A provider or callback failure message, trimmed to a readable length. */
export function readReason(params: SearchParams): string | null {
  const reason = params.reason;
  return typeof reason === "string" && reason.length > 0 ? reason.slice(0, 300) : null;
}
