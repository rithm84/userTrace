CREATE TABLE "public"."voice_connections" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "interview_session_id"      uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL DEFAULT 0,
  "machine_version"           smallint                 NOT NULL DEFAULT 1,
  "voice_run_id"              uuid                     NOT NULL,
  "participant_connection_id" uuid                     NOT NULL,
  "provider_session_ref"      text,
  "superseded_by_id"          uuid,
  "closed_at"                 timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "voice_connections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "voice_connections_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "voice_connections_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_connections_superseded_by_id_fkey" FOREIGN KEY (superseded_by_id) REFERENCES public.voice_connections(id),
  CONSTRAINT "voice_connections_voice_run_id_key" UNIQUE (voice_run_id)
);

ALTER TABLE "public"."voice_connections"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."voice_connections"
  ADD COLUMN "state" public.voice_connection_state NOT NULL;

CREATE UNIQUE INDEX voice_connections_one_current ON public.voice_connections USING btree (interview_session_id)
  WHERE (state <> 'closed'::public.voice_connection_state);

CREATE INDEX voice_connections_session_idx ON public.voice_connections USING btree (interview_session_id, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_connections" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_connections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_connections" TO "postgres";

REVOKE ALL ON TABLE "public"."voice_connections" FROM "anon", "authenticated";
