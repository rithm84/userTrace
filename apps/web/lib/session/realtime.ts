"use client";

import { REALTIME_SUBSCRIBE_STATES, type RealtimeChannel, type SupabaseClient } from "@supabase/supabase-js";

import {
  SESSION_PROJECTION_TABLE,
  sessionProjectionSchema,
  type SessionProjection,
} from "@/lib/api/schemas";
import { createSupabaseBrowserClient } from "@/lib/supabase/browser";

/**
 * Live session updates.
 *
 * Supabase Realtime carries committed projection rows, row-level security
 * scopes them to this participant, and the durable Postgres projection stays
 * authoritative. A page with no signed-in session, a channel that never joins,
 * or a row that does not match the contract reports `unavailable`: the tab then
 * shows what the API returned and invents nothing.
 */

export type RealtimeStatus = "connecting" | "live" | "unavailable";

export type SubscribeParameters = Readonly<{
  sessionId: string;
  onProjection: (projection: SessionProjection) => void;
  onStatus: (status: RealtimeStatus) => void;
}>;

export function subscribeToSession(parameters: SubscribeParameters): () => void {
  const supabase = createSupabaseBrowserClient();
  parameters.onStatus("connecting");
  let channel: RealtimeChannel | null = null;
  let cancelled = false;

  void (async () => {
    // A channel join carries the participant's token only if the client already
    // holds it, and the browser client loads its session from cookies
    // asynchronously. A join without it runs as `anon`, which row-level security
    // filters to nothing while the channel still reports SUBSCRIBED.
    const { data, error } = await supabase.auth.getSession();
    if (error !== null || data.session === null) {
      console.error("session_realtime_unauthenticated", {
        sessionId: parameters.sessionId,
        error: error?.message ?? "no signed-in session",
      });
      parameters.onStatus("unavailable");
      return;
    }
    await supabase.realtime.setAuth();
    if (cancelled) return;
    channel = join(supabase, parameters);
  })().catch((error: unknown) => {
    console.error("session_realtime_auth_failed", { sessionId: parameters.sessionId, error });
    parameters.onStatus("unavailable");
  });

  return () => {
    cancelled = true;
    if (channel !== null) {
      void supabase.removeChannel(channel);
    }
  };
}

function join(supabase: SupabaseClient, parameters: SubscribeParameters): RealtimeChannel {
  return supabase
    .channel(`session:${parameters.sessionId}`)
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: SESSION_PROJECTION_TABLE,
        filter: `session_id=eq.${parameters.sessionId}`,
      },
      (payload) => {
        const parsed = sessionProjectionSchema.safeParse(
          (payload.new as Record<string, unknown>).projection,
        );
        if (!parsed.success) {
          console.error("session_projection_rejected", {
            sessionId: parameters.sessionId,
            issues: parsed.error.issues,
          });
          parameters.onStatus("unavailable");
          return;
        }
        parameters.onProjection(parsed.data);
      },
    )
    .subscribe((status) => {
      if (status === REALTIME_SUBSCRIBE_STATES.SUBSCRIBED) {
        parameters.onStatus("live");
        return;
      }
      if (
        status === REALTIME_SUBSCRIBE_STATES.CHANNEL_ERROR ||
        status === REALTIME_SUBSCRIBE_STATES.TIMED_OUT ||
        status === REALTIME_SUBSCRIBE_STATES.CLOSED
      ) {
        parameters.onStatus("unavailable");
      }
    });
}
