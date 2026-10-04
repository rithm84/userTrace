CREATE TABLE "public"."participant_preflight_records" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "client_user_id"                uuid                     NOT NULL,
  "browser_name"                  text                     NOT NULL,
  "browser_major_version"         integer                  NOT NULL,
  "browser_supported"             boolean                  NOT NULL,
  "extension_installed"           boolean                  NOT NULL,
  "extension_installation_id"     uuid,
  "account_linked"                boolean                  NOT NULL,
  "host_permission_granted"       boolean                  NOT NULL,
  "microphone_permission_granted" boolean                  NOT NULL,
  "control_plane_reachable"       boolean                  NOT NULL,
  "reported_protocol_version"     text                     NOT NULL,
  "protocol_supported"            boolean                  NOT NULL,
  "passed"                        boolean                  NOT NULL,
  "failed_checks"                 text[]                   NOT NULL,
  "recorded_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "participant_preflight_failed_checks_match" CHECK ((passed = (cardinality(failed_checks) = 0))),
  CONSTRAINT "participant_preflight_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "participant_preflight_records_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "participant_preflight_records_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id),
  CONSTRAINT "participant_preflight_records_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "participant_preflight_records_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."participant_preflight_records"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX participant_preflight_records_session_idx ON public.participant_preflight_records USING btree (interview_session_id, recorded_at DESC);

CREATE TRIGGER participant_preflight_records_immutable
  BEFORE DELETE OR UPDATE ON public.participant_preflight_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."participant_preflight_records" TO "service_role";

REVOKE ALL ON TABLE "public"."participant_preflight_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."participant_preflight_records" TO "postgres";

REVOKE ALL ON TABLE "public"."participant_preflight_records" FROM "anon", "authenticated";
