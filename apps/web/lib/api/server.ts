import "server-only";

import { readServerEnvironment, usesFixtureControlPlane } from "@/lib/env";

import { HttpControlPlaneClient, type ControlPlaneClient } from "./client";
import { FixtureControlPlaneClient } from "./fixture";
import { FIXTURE_CLIENT_ID } from "./fixture-data";

const READ_ATTEMPTS = 3;

/**
 * The control-plane client for one request, authenticated as the caller.
 * `UTRACE_CONTROL_PLANE_FIXTURES=1` swaps in the Shopalytics fixture so the
 * product surfaces can be previewed and tested without the API.
 */
export function createControlPlaneClient(accessToken: string): ControlPlaneClient {
  if (usesFixtureControlPlane()) {
    return new FixtureControlPlaneClient();
  }
  const environment = readServerEnvironment();
  return new HttpControlPlaneClient({
    baseUrl: environment.apiBaseUrl,
    accessToken,
    readAttempts: READ_ATTEMPTS,
    fetchImplementation: fetch,
  });
}

/**
 * The single uTrace Client this deployment's dashboard serves. In fixture mode
 * it is the Shopalytics fixture's own identifier, which is the only client that
 * fixture answers for.
 */
export function activeClientId(): string {
  return usesFixtureControlPlane() ? FIXTURE_CLIENT_ID : readServerEnvironment().clientId;
}
