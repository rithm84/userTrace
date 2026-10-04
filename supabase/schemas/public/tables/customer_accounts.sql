CREATE TABLE "public"."customer_accounts" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "external_account_key" text                     NOT NULL,
  "name"                 text                     NOT NULL,
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "customer_accounts_client_id_external_account_key_key" UNIQUE (client_id, external_account_key),
  CONSTRAINT "customer_accounts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "customer_accounts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."customer_accounts"
  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "customer_accounts_member_select" ON "public"."customer_accounts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_accounts" TO "service_role";

REVOKE ALL ON TABLE "public"."customer_accounts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."customer_accounts" TO "authenticated";

REVOKE ALL ON TABLE "public"."customer_accounts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_accounts" TO "postgres";

REVOKE ALL ON TABLE "public"."customer_accounts" FROM "anon";
