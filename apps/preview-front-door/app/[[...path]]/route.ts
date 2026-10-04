import { handleProxyRequest } from "@/lib/door";

/**
 * Every other request on a session origin: verified, routed to one committed route
 * revision, and streamed from the client product's preview runtime.
 *
 * Vercel functions cannot proxy a WebSocket upgrade, so this handler serves HTTP only.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = handleProxyRequest;
export const HEAD = handleProxyRequest;
export const POST = handleProxyRequest;
export const PUT = handleProxyRequest;
export const PATCH = handleProxyRequest;
export const DELETE = handleProxyRequest;
export const OPTIONS = handleProxyRequest;
