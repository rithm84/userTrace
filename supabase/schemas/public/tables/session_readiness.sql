CREATE TABLE "public"."session_readiness" (
  "interview_session_id"      uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "readiness_revision"        bigint                   NOT NULL DEFAULT 0,
  "participant_authenticated" boolean                  NOT NULL DEFAULT false,
  "voice_transport_ready"     boolean                  NOT NULL DEFAULT false,
  "sideband_attached"         boolean                  NOT NULL DEFAULT false,
  "backend_context_current"   boolean                  NOT NULL DEFAULT false,
  "preview_allocation_ready"  boolean                  NOT NULL DEFAULT false,
  "browser_sdk_ready"         boolean                  NOT NULL DEFAULT false,
  "server_sdk_ready"          boolean                  NOT NULL DEFAULT false,
  "trace_correlation_ready"   boolean                  NOT NULL DEFAULT false,
  "conversational_ready"      boolean                  NOT NULL DEFAULT false,
  "observation_ready"         boolean                  NOT NULL DEFAULT false,
  "blockers"                  text[]                   NOT NULL DEFAULT '{}'::text[],
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "session_readiness_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "session_readiness_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "session_readiness_pkey" PRIMARY KEY (interview_session_id)
);

ALTER TABLE "public"."session_readiness"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."session_readiness"
  ADD COLUMN "extension_readiness" public.extension_readiness_state NOT NULL DEFAULT 'withdrawn'::public.extension_readiness_state;

CREATE TRIGGER session_readiness_notify_extension_control
  AFTER INSERT OR UPDATE OF readiness_revision ON public.session_readiness
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_extension_control('interview_session_id');

CREATE TRIGGER session_readiness_notify_voice_sideband
  AFTER INSERT OR UPDATE OF readiness_revision ON public.session_readiness
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_voice_sideband('interview_session_id');

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_readiness" TO "service_role";

REVOKE ALL ON TABLE "public"."session_readiness" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_readiness" TO "postgres";

REVOKE ALL ON TABLE "public"."session_readiness" FROM "anon", "authenticated";
