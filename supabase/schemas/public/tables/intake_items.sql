CREATE TABLE "public"."intake_items" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "machine_version"          smallint                 NOT NULL DEFAULT 1,
  "source_provider"          text                     NOT NULL,
  "source_channel_ref"       text                     NOT NULL,
  "source_batch_digest"      text                     NOT NULL,
  "author_provider_identity" text                     NOT NULL,
  "client_user_id"           uuid,
  "customer_account_id"      uuid,
  "payload"                  jsonb                    NOT NULL,
  "product_signal_id"        uuid,
  "blocked_reason"           text,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_items_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "intake_items_client_id_source_batch_digest_key" UNIQUE (client_id, source_batch_digest),
  CONSTRAINT "intake_items_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "intake_items_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "intake_items_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."intake_items"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."intake_items"
  ADD COLUMN "state" public.intake_item_state NOT NULL;

ALTER TABLE "public"."intake_items"
  ADD COLUMN "routing_outcome" public.routing_outcome;

CREATE INDEX intake_items_client_state_idx ON public.intake_items USING btree (client_id, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_items" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_items" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_items" TO "postgres";

REVOKE ALL ON TABLE "public"."intake_items" FROM "anon", "authenticated";
