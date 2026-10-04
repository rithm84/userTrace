/** Typed failures from the control-plane HTTP contract. */

export type ControlPlaneErrorCode =
  | "unauthenticated"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "rejected"
  | "unavailable"
  | "transport"
  | "invalid_response";

export type ControlPlaneErrorContext = Readonly<{
  code: ControlPlaneErrorCode;
  method: string;
  path: string;
  status: number | null;
  responseBody: string | null;
}>;

export class ControlPlaneError extends Error {
  readonly code: ControlPlaneErrorCode;
  readonly method: string;
  readonly path: string;
  readonly status: number | null;
  readonly responseBody: string | null;

  constructor(context: ControlPlaneErrorContext, message: string, options: { cause?: unknown }) {
    super(message, options);
    this.name = "ControlPlaneError";
    this.code = context.code;
    this.method = context.method;
    this.path = context.path;
    this.status = context.status;
    this.responseBody = context.responseBody;
  }
}

export function codeForStatus(status: number): ControlPlaneErrorCode {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status >= 400 && status < 500) return "rejected";
  return "unavailable";
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ControlPlaneError && error.code === "not_found";
}
