/**
 * The build-time network configuration, injected by `scripts/build.ts` and
 * validated again here so a mistaken build fails at load rather than at the
 * first request.
 */
import { parseBuildOrigins, type BuildOrigins } from "../build/buildOrigins";

declare const __UTRACE_BUILD_CONFIG__: unknown;
declare const __UTRACE_EXTENSION_VERSION__: string;

export const BUILD_ORIGINS: BuildOrigins = parseBuildOrigins(__UTRACE_BUILD_CONFIG__);
export const EXTENSION_VERSION: string = __UTRACE_EXTENSION_VERSION__;

/** Possession proofs name origins, which the control plane checks exactly; requests keep the base path. */
export const API_AUDIENCE: string = new URL(BUILD_ORIGINS.apiBaseUrl).origin;
export const CONTROL_SOCKET_AUDIENCE: string = new URL(BUILD_ORIGINS.controlSocketBaseUrl).origin;

export const CONTROL_SOCKET_PATH = "/v1/extension/control";
export const INSTALLATIONS_PATH = "/v1/extension/installations";

export function controlSocketUrl(): string {
  return `${BUILD_ORIGINS.controlSocketBaseUrl}${CONTROL_SOCKET_PATH}`;
}

export function installationsUrl(): string {
  return `${BUILD_ORIGINS.apiBaseUrl}${INSTALLATIONS_PATH}`;
}

/** True only for the exact origins `externally_connectable` allows. */
export function isSessionTabOrigin(origin: string): boolean {
  return BUILD_ORIGINS.sessionTabOrigins.includes(origin);
}
