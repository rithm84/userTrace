CREATE TABLE "public"."outbox" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "job_id"         uuid,
  "command_id"     uuid,
  "available_at"   timestamp with time zone NOT NULL DEFAULT now(),
  "enqueued_at"    timestamp with time zone,
  "dispatched_at"  timestamp with time zone,
  "attempts"       integer                  NOT NULL DEFAULT 0,
  "last_error"     text,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outbox_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "outbox_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id),
  CONSTRAINT "outbox_exactly_one_intent" CHECK ((num_nonnulls(job_id, command_id) = 1)),
  CONSTRAINT "outbox_job_id_fkey" FOREIGN KEY (job_id) REFERENCES public.jobs(id),
  CONSTRAINT "outbox_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."outbox"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."outbox"
  ADD COLUMN "state" public.outbox_state NOT NULL;

CREATE INDEX outbox_pending_idx ON public.outbox USING btree (available_at, created_at)
  WHERE (state = 'pending'::public.outbox_state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outbox" TO "service_role";

REVOKE ALL ON TABLE "public"."outbox" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outbox" TO "postgres";

REVOKE ALL ON TABLE "public"."outbox" FROM "anon", "authenticated";
