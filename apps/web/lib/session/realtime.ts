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
 *
 * Realtime does not replay rows committed while a channel was not joined, so
 * every join asks the tab to re-read the projection. A channel the server closes
 * joins again with the client's current token: the server closes one whose token
 * expires, and a token refreshed while the channel was still joining never
 * reaches it, so without rejoining the tab would stop hearing about the session
 * an hour after the participant last signed in.
 */

export type RealtimeStatus = "connecting" | "live" | "unavailable";

export type SubscribeParameters = Readonly<{
  sessionId: string;
  onProjection: (projection: SessionProjection) => void;
  onStatus: (status: RealtimeStatus) => void;
  /** Called on every join, so the tab can re-read what it missed while not joined. */
  onJoined: () => void;
}>;

/** How long a lost channel waits before it joins again. */
const REJOIN_DELAY_MS = 2_000;

export function subscribeToSession(parameters: SubscribeParameters): () => void {
  const supabase = createSupabaseBrowserClient();
  parameters.onStatus("connecting");
  let channel: RealtimeChannel | null = null;
  let rejoin: number | undefined;
  let cancelled = false;

  const lost = (lostChannel: RealtimeChannel, status: string): void => {
    // Removing a lost channel closes it once more; only the current channel counts.
    if (cancelled || channel !== lostChannel) return;
    console.warn("session_realtime_lost", { sessionId: parameters.sessionId, status });
    parameters.onStatus("unavailable");
    channel = null;
    void supabase.removeChannel(lostChannel);
    rejoin = window.setTimeout(connect, REJOIN_DELAY_MS);
  };

  function connect(): void {
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
      channel = join(supabase, parameters, lost);
    })().catch((error: unknown) => {
      console.error("session_realtime_auth_failed", { sessionId: parameters.sessionId, error });
      parameters.onStatus("unavailable");
    });
  }

  connect();
  return () => {
    cancelled = true;
    window.clearTimeout(rejoin);
    if (channel !== null) {
      void supabase.removeChannel(channel);
    }
  };
}

function join(
  supabase: SupabaseClient,
  parameters: SubscribeParameters,
  lost: (channel: RealtimeChannel, status: string) => void,
): RealtimeChannel {
  const channel = supabase
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
    );
  return channel.subscribe((status) => {
    if (status === REALTIME_SUBSCRIBE_STATES.SUBSCRIBED) {
      parameters.onStatus("live");
      parameters.onJoined();
      return;
    }
    if (
      status === REALTIME_SUBSCRIBE_STATES.CHANNEL_ERROR ||
      status === REALTIME_SUBSCRIBE_STATES.TIMED_OUT ||
      status === REALTIME_SUBSCRIBE_STATES.CLOSED
    ) {
      lost(channel, status);
    }
  });
}
