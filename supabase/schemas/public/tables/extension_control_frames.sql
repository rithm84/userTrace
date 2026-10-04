CREATE TABLE "public"."extension_control_frames" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid,
  "extension_installation_id" uuid                     NOT NULL,
  "interview_session_id"      uuid,
  "sequence"                  bigint                   NOT NULL,
  "kind"                      text                     NOT NULL,
  "payload"                   jsonb                    NOT NULL,
  "sent_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  "acknowledged_at"           timestamp with time zone,
  "superseded_at"             timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_control_frames_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "extension_control_frames_pkey" PRIMARY KEY (id),
  CONSTRAINT "extension_control_frames_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id),
  CONSTRAINT "extension_control_frames_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id)
);

ALTER TABLE "public"."extension_control_frames"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX extension_control_frames_live_sequence ON public.extension_control_frames USING btree (extension_installation_id, SEQUENCE)
  WHERE (superseded_at IS NULL);

CREATE INDEX extension_control_frames_stream_idx ON public.extension_control_frames USING btree (extension_installation_id, SEQUENCE DESC);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_frames" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_control_frames" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_frames" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_control_frames" FROM "anon", "authenticated";
