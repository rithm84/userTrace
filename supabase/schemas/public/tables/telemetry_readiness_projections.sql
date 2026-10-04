CREATE TABLE "public"."telemetry_readiness_projections" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "activation_id"             uuid                     NOT NULL,
  "interview_session_id"      uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL,
  "ready"                     boolean                  NOT NULL,
  "contributing_resource_ids" jsonb                    NOT NULL,
  "accepted_watermarks"       jsonb                    NOT NULL,
  "probe_id"                  uuid,
  "failures"                  jsonb                    NOT NULL,
  "checked_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at"                timestamp with time zone NOT NULL,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_readiness_projections_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id),
  CONSTRAINT "telemetry_readiness_projections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_readiness_projections_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "telemetry_readiness_projections_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_readiness_revision_positive" CHECK ((revision >= 1))
);

ALTER TABLE "public"."telemetry_readiness_projections"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD COLUMN "kind" public.telemetry_readiness_kind NOT NULL;

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD CONSTRAINT "telemetry_readiness_projectio_client_id_activation_id_kind__key" UNIQUE (client_id, activation_id, kind, revision);

CREATE INDEX telemetry_readiness_current_idx ON public.telemetry_readiness_projections USING btree (activation_id, kind, revision DESC);

CREATE INDEX telemetry_readiness_session_idx ON public.telemetry_readiness_projections USING btree (interview_session_id, kind, revision DESC);

CREATE TRIGGER telemetry_readiness_projections_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_readiness_projections
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_readiness_projections" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_readiness_projections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_readiness_projections" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_readiness_projections" FROM "anon", "authenticated";
