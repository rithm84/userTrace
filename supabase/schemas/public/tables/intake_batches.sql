CREATE TABLE "public"."intake_batches" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "provider"                 text                     NOT NULL,
  "channel_ref"              text                     NOT NULL,
  "author_provider_identity" text                     NOT NULL,
  "seal_job_id"              uuid,
  "intake_item_id"           uuid,
  "message_count"            integer                  NOT NULL DEFAULT 0,
  "opened_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "seal_not_before"          timestamp with time zone NOT NULL,
  "seal_deadline_at"         timestamp with time zone NOT NULL,
  "sealed_at"                timestamp with time zone,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_batches_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "intake_batches_pkey" PRIMARY KEY (id),
  CONSTRAINT "intake_batches_seal_window_ordered" CHECK ((seal_deadline_at >= seal_not_before)),
  CONSTRAINT "intake_batches_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id),
  CONSTRAINT "intake_batches_seal_job_id_fkey" FOREIGN KEY (seal_job_id) REFERENCES public.jobs(id)
);

ALTER TABLE "public"."intake_batches"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."intake_batches"
  ADD COLUMN "state" public.intake_batch_state NOT NULL;

CREATE UNIQUE INDEX intake_batches_one_open ON public.intake_batches USING btree (client_id, PROVIDER, channel_ref, author_provider_identity)
  WHERE (state = 'open'::public.intake_batch_state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_batches" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_batches" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_batches" TO "postgres";

REVOKE ALL ON TABLE "public"."intake_batches" FROM "anon", "authenticated";
