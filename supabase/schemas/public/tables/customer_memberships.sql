CREATE TABLE "public"."customer_memberships" (
  "id"                  uuid                     NOT NULL,
  "client_id"           uuid                     NOT NULL,
  "customer_account_id" uuid                     NOT NULL,
  "client_user_id"      uuid                     NOT NULL,
  "schema_version"      smallint                 NOT NULL DEFAULT 1,
  "created_at"          timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "customer_memberships_client_id_customer_account_id_client_u_key" UNIQUE (client_id, customer_account_id, client_user_id),
  CONSTRAINT "customer_memberships_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "customer_memberships_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "customer_memberships_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "customer_memberships_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."customer_memberships"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX customer_memberships_client_user_idx ON public.customer_memberships USING btree (client_user_id);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_memberships" TO "service_role";

REVOKE ALL ON TABLE "public"."customer_memberships" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_memberships" TO "postgres";

REVOKE ALL ON TABLE "public"."customer_memberships" FROM "anon", "authenticated";
