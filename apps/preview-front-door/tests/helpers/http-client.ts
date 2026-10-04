import { request as httpRequest } from "node:http";

/**
 * A minimal HTTP client that sends a chosen `Host` header to a loopback port.
 *
 * The door decides everything from the Host header, and a real participant reaches it as
 * `<opaque>.preview.utrace.dev`. These tests therefore connect to 127.0.0.1 and set the
 * session origin in the Host header, exactly as Vercel's edge presents it.
 */

export interface DoorResponse {
  readonly status: number;
  readonly headers: Readonly<Record<string, string | string[]>>;
  readonly setCookies: readonly string[];
  readonly body: string;
}

export async function doorRequest(params: {
  port: number;
  hostHeader: string;
  method: string;
  path: string;
  headers: Readonly<Record<string, string>>;
  body: string | null;
}): Promise<DoorResponse> {
  return new Promise<DoorResponse>((resolve, reject) => {
    const outgoing = httpRequest(
      {
        host: "127.0.0.1",
        port: params.port,
        method: params.method,
        path: params.path,
        headers: { ...params.headers, host: params.hostHeader },
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () => {
          const headers: Record<string, string | string[]> = {};
          for (const [name, value] of Object.entries(response.headers)) {
            if (value !== undefined) {
              headers[name] = value;
            }
          }
          resolve({
            status: response.statusCode ?? 0,
            headers,
            setCookies: response.headers["set-cookie"] ?? [],
            body: Buffer.concat(chunks).toString("utf8"),
          });
        });
      },
    );
    outgoing.on("error", reject);
    if (params.body !== null) {
      outgoing.write(params.body);
    }
    outgoing.end();
  });
}
