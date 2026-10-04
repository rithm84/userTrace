CREATE TABLE "public"."deliveries" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "case_file_id"          uuid                     NOT NULL,
  "validation_attempt_id" uuid                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "repository_ref"        text                     NOT NULL,
  "branch_ref"            text,
  "head_commit"           text                     NOT NULL,
  "base_revision"         text                     NOT NULL,
  "pull_request_ref"      text,
  "idempotency_marker"    text                     NOT NULL,
  "blocker_reason"        text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "command_id"            uuid,
  "base_branch"           text,
  "case_file_url"         text,
  "pull_request_number"   integer,
  "pull_request_node_id"  text,
  "verified_head_sha"     text,
  "preflight"             jsonb,
  "reconciliation"        jsonb,
  CONSTRAINT "deliveries_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "deliveries_idempotency_marker_key" UNIQUE (idempotency_marker),
  CONSTRAINT "deliveries_pkey" PRIMARY KEY (id),
  CONSTRAINT "deliveries_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "deliveries_validation_attempt_id_fkey" FOREIGN KEY (validation_attempt_id) REFERENCES public.validation_attempts(id)
);

ALTER TABLE "public"."deliveries"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."deliveries"
  ADD COLUMN "state" public.delivery_state NOT NULL;

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_blocked_carries_reason" CHECK (((state <> 'blocked'::public.delivery_state) OR (blocker_reason IS NOT NULL)));

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_delivered_identities" CHECK (((state <> 'delivered'::public.delivery_state) OR ((pull_request_ref IS NOT NULL) AND (pull_request_number IS
    NOT NULL) AND (verified_head_sha IS NOT NULL) AND (branch_ref IS NOT NULL))));

CREATE INDEX deliveries_signal_idx ON public.deliveries USING btree (product_signal_id, state);

CREATE POLICY "deliveries_member_select" ON "public"."deliveries"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."deliveries" TO "service_role";

REVOKE ALL ON TABLE "public"."deliveries" FROM "authenticated";

GRANT SELECT ON TABLE "public"."deliveries" TO "authenticated";

REVOKE ALL ON TABLE "public"."deliveries" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."deliveries" TO "postgres";

REVOKE ALL ON TABLE "public"."deliveries" FROM "anon";
