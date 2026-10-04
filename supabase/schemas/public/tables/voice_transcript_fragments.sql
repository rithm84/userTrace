CREATE TABLE "public"."voice_transcript_fragments" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "interview_session_id" uuid                     NOT NULL,
  "voice_connection_id"  uuid                     NOT NULL,
  "voice_run_id"         uuid                     NOT NULL,
  "provider_session_ref" text                     NOT NULL,
  "provider_event_type"  text                     NOT NULL,
  "start_ms"             integer                  NOT NULL,
  "end_ms"               integer                  NOT NULL,
  "fragment_text"        text                     NOT NULL,
  "provenance"           jsonb                    NOT NULL,
  "recorded_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "voice_transcript_fragments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "voice_transcript_fragments_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "voice_transcript_fragments_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_transcript_fragments_span_ordered" CHECK ((end_ms >= start_ms)),
  CONSTRAINT "voice_transcript_fragments_voice_connection_id_fkey" FOREIGN KEY (voice_connection_id) REFERENCES public.voice_connections(id),
  CONSTRAINT "voice_transcript_fragments_voice_connection_id_provider_eve_key" UNIQUE (voice_connection_id, provider_event_type, start_ms, end_ms)
);

ALTER TABLE "public"."voice_transcript_fragments"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."voice_transcript_fragments"
  ADD COLUMN "speaker" public.transcript_speaker NOT NULL;

CREATE INDEX voice_transcript_fragments_session_idx ON public.voice_transcript_fragments USING btree (interview_session_id, start_ms);

CREATE TRIGGER voice_transcript_fragments_immutable
  BEFORE DELETE OR UPDATE ON public.voice_transcript_fragments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_transcript_fragments" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_transcript_fragments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_transcript_fragments" TO "postgres";

REVOKE ALL ON TABLE "public"."voice_transcript_fragments" FROM "anon", "authenticated";
