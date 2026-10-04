CREATE TABLE "public"."jobs" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "job_type"              text                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "input"                 jsonb                    NOT NULL,
  "deadline_at"           timestamp with time zone NOT NULL,
  "timeout_seconds"       integer                  NOT NULL,
  "max_runs"              integer                  NOT NULL,
  "run_count"             integer                  NOT NULL DEFAULT 0,
  "resource_budget"       jsonb                    NOT NULL,
  "retryable_categories"  text[]                   NOT NULL,
  "backoff_base_seconds"  integer                  NOT NULL,
  "backoff_max_seconds"   integer                  NOT NULL,
  "concurrency_key"       text,
  "cancellation_scope_id" uuid,
  "not_before"            timestamp with time zone NOT NULL DEFAULT now(),
  "last_failure_category" text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "jobs_backoff_ordered" CHECK (((backoff_base_seconds > 0) AND (backoff_max_seconds >= backoff_base_seconds))),
  CONSTRAINT "jobs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "jobs_max_runs_positive" CHECK ((max_runs > 0)),
  CONSTRAINT "jobs_pkey" PRIMARY KEY (id),
  CONSTRAINT "jobs_run_count_within_max" CHECK (((run_count >= 0) AND (run_count <= max_runs))),
  CONSTRAINT "jobs_timeout_positive" CHECK ((timeout_seconds > 0))
);

ALTER TABLE "public"."jobs"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."jobs"
  ADD COLUMN "state" public.job_state NOT NULL;

ALTER TABLE "public"."jobs"
  ADD COLUMN "last_retry_advice" public.retry_advice;

CREATE INDEX jobs_cancellation_scope_idx ON public.jobs USING btree (cancellation_scope_id)
  WHERE (state = ANY (ARRAY['pending'::public.job_state, 'ready'::public.job_state, 'leased'::public.job_state]));

CREATE INDEX jobs_claimable_idx ON public.jobs USING btree (not_before, created_at)
  WHERE (state = 'ready'::public.job_state);

CREATE UNIQUE INDEX jobs_one_leased_per_concurrency_key ON public.jobs USING btree (client_id, concurrency_key)
  WHERE ((state = 'leased'::public.job_state) AND (concurrency_key IS NOT NULL));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."jobs" TO "service_role";

REVOKE ALL ON TABLE "public"."jobs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."jobs" TO "postgres";

REVOKE ALL ON TABLE "public"."jobs" FROM "anon", "authenticated";
