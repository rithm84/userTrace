CREATE TABLE "public"."intake_author_attributions" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "provider"                 text                     NOT NULL,
  "author_provider_identity" text                     NOT NULL,
  "client_user_id"           uuid                     NOT NULL,
  "customer_account_id"      uuid                     NOT NULL,
  "reason"                   text                     NOT NULL,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_author_attributions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "intake_author_attributions_client_id_provider_author_provid_key" UNIQUE (client_id, PROVIDER, author_provider_identity),
  CONSTRAINT "intake_author_attributions_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "intake_author_attributions_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "intake_author_attributions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."intake_author_attributions"
  ENABLE ROW LEVEL SECURITY;

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_author_attributions" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_author_attributions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_author_attributions" TO "postgres";

REVOKE ALL ON TABLE "public"."intake_author_attributions" FROM "anon", "authenticated";
