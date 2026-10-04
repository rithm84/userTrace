CREATE TABLE "public"."job_runs" (
  "id"               uuid                     NOT NULL,
  "client_id"        uuid                     NOT NULL,
  "job_id"           uuid                     NOT NULL,
  "revision"         bigint                   NOT NULL DEFAULT 0,
  "machine_version"  smallint                 NOT NULL DEFAULT 1,
  "fencing_token"    bigint                   NOT NULL,
  "worker_id"        text                     NOT NULL,
  "lease_expires_at" timestamp with time zone NOT NULL,
  "heartbeat_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "started_at"       timestamp with time zone NOT NULL DEFAULT now(),
  "finished_at"      timestamp with time zone,
  "failure_category" text,
  "schema_version"   smallint                 NOT NULL DEFAULT 1,
  "created_at"       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"       timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "job_runs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "job_runs_job_id_fencing_token_key" UNIQUE (job_id, fencing_token),
  CONSTRAINT "job_runs_pkey" PRIMARY KEY (id),
  CONSTRAINT "job_runs_job_id_fkey" FOREIGN KEY (job_id) REFERENCES public.jobs(id)
);

ALTER TABLE "public"."job_runs"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."job_runs"
  ADD COLUMN "state" public.job_run_state NOT NULL;

ALTER TABLE "public"."job_runs"
  ADD COLUMN "retry_advice" public.retry_advice;

CREATE INDEX job_runs_lease_scan_idx ON public.job_runs USING btree (lease_expires_at)
  WHERE (state = 'leased'::public.job_run_state);

CREATE UNIQUE INDEX job_runs_one_leased ON public.job_runs USING btree (job_id)
  WHERE (state = 'leased'::public.job_run_state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."job_runs" TO "service_role";

REVOKE ALL ON TABLE "public"."job_runs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."job_runs" TO "postgres";

REVOKE ALL ON TABLE "public"."job_runs" FROM "anon", "authenticated";
