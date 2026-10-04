CREATE TABLE "public"."implementation_check_runs" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "implementation_attempt_id" uuid                     NOT NULL,
  "environment_lease_id"      uuid                     NOT NULL,
  "phase"                     text                     NOT NULL,
  "check_name"                text                     NOT NULL,
  "command"                   text                     NOT NULL,
  "exit_code"                 integer                  NOT NULL,
  "duration_seconds"          double precision         NOT NULL,
  "log_artifact_id"           uuid,
  "cycle_index"               integer                  NOT NULL,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "implementation_check_runs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "implementation_check_runs_environment_lease_id_fkey" FOREIGN KEY (environment_lease_id) REFERENCES public.environment_leases(id),
  CONSTRAINT "implementation_check_runs_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "implementation_check_runs_log_artifact_id_fkey" FOREIGN KEY (log_artifact_id) REFERENCES public.artifacts(id),
  CONSTRAINT "implementation_check_runs_phase_known" CHECK ((phase = ANY (ARRAY['baseline'::text, 'candidate'::text]))),
  CONSTRAINT "implementation_check_runs_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."implementation_check_runs"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX implementation_check_runs_attempt_idx ON public.implementation_check_runs USING btree (implementation_attempt_id, phase, cycle_index);

CREATE POLICY "implementation_check_runs_member_select" ON "public"."implementation_check_runs"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_check_runs" TO "service_role";

REVOKE ALL ON TABLE "public"."implementation_check_runs" FROM "authenticated";

GRANT SELECT ON TABLE "public"."implementation_check_runs" TO "authenticated";

REVOKE ALL ON TABLE "public"."implementation_check_runs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_check_runs" TO "postgres";

REVOKE ALL ON TABLE "public"."implementation_check_runs" FROM "anon";
