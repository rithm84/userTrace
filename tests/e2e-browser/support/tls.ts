/**
 * TLS termination in front of the preview front door.
 *
 * The door issues a `__Host-` session cookie, which a browser stores only on a
 * secure origin. A session origin is a subdomain, so `http://localhost`'s
 * exemption does not apply to it and the cookie would be dropped: the journey
 * would then prove nothing about routing, because every request after the claim
 * would arrive without a session. The terminator therefore serves the session
 * origin over real HTTPS with a certificate generated for this run, and Chrome
 * is launched to accept it.
 *
 * It rewrites nothing. It forwards the method, the path, the body, and every
 * header including `Host`, so the door sees exactly the request the browser
 * made and resolves the same session origin it would behind a real edge.
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { createServer as createHttpsServer, type Server } from "node:https";
import { request as httpRequest } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";

export interface TlsTerminator {
  readonly port: number;
  readonly close: () => Promise<void>;
}

/** Generates a self-signed certificate for `*.<apex>` with OpenSSL. */
function createCertificate(apex: string): { readonly key: string; readonly cert: string } {
  const directory = mkdtempSync(join(tmpdir(), "utrace-e2e-tls-"));
  const keyPath = join(directory, "key.pem");
  const certPath = join(directory, "cert.pem");
  const result = spawnSync(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-keyout",
      keyPath,
      "-out",
      certPath,
      "-days",
      "1",
      "-subj",
      `/CN=*.${apex}`,
      "-addext",
      `subjectAltName=DNS:*.${apex},DNS:${apex}`,
    ],
    { encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(`openssl could not generate the journey's certificate: ${result.stderr}`);
  }
  return { key: readFileSync(keyPath, "utf8"), cert: readFileSync(certPath, "utf8") };
}

export async function startTlsTerminator(params: {
  readonly port: number;
  readonly apex: string;
  readonly upstreamPort: number;
}): Promise<TlsTerminator> {
  const { key, cert } = createCertificate(params.apex);
  const server: Server = createHttpsServer({ key, cert }, (incoming, response) => {
    const forwarded = httpRequest(
      {
        host: "127.0.0.1",
        port: params.upstreamPort,
        method: incoming.method,
        path: incoming.url,
        headers: incoming.headers,
      },
      (upstream) => {
        response.writeHead(upstream.statusCode ?? 502, upstream.headers);
        upstream.pipe(response);
      },
    );
    forwarded.on("error", (error: Error) => {
      response.writeHead(502, { "content-type": "text/plain" });
      response.end(`the journey's TLS terminator could not reach the door: ${error.message}`);
    });
    incoming.pipe(forwarded);
  });

  await new Promise<void>((resolve) => {
    server.listen(params.port, "127.0.0.1", resolve);
  });

  return {
    port: params.port,
    close: (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
}
