CREATE TABLE "public"."voice_playback_segments" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "interview_session_id" uuid                     NOT NULL,
  "voice_connection_id"  uuid                     NOT NULL,
  "start_ms"             integer                  NOT NULL,
  "end_ms"               integer                  NOT NULL,
  "audio_byte_length"    integer                  NOT NULL,
  "recorded_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "voice_playback_segments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "voice_playback_segments_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "voice_playback_segments_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_playback_segments_span_ordered" CHECK ((end_ms >= start_ms)),
  CONSTRAINT "voice_playback_segments_voice_connection_id_fkey" FOREIGN KEY (voice_connection_id) REFERENCES public.voice_connections(id),
  CONSTRAINT "voice_playback_segments_voice_connection_id_start_ms_end_ms_key" UNIQUE (voice_connection_id, start_ms, end_ms)
);

ALTER TABLE "public"."voice_playback_segments"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."voice_playback_segments"
  ADD COLUMN "delivery" public.playback_delivery NOT NULL;

CREATE TRIGGER voice_playback_segments_immutable
  BEFORE DELETE OR UPDATE ON public.voice_playback_segments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_playback_segments" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_playback_segments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_playback_segments" TO "postgres";

REVOKE ALL ON TABLE "public"."voice_playback_segments" FROM "anon", "authenticated";
