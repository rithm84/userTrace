import { handleTelemetryCredentialRelay } from "@/lib/relays";

/**
 * `POST /__utrace/telemetry-credential` — the one-time exchange of a verified document
 * attestation for the short-lived, write-only browser telemetry credential.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = handleTelemetryCredentialRelay;
