CREATE TABLE "public"."client_users" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "utrace_user_id" uuid                     NOT NULL,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "client_users_client_id_utrace_user_id_key" UNIQUE (client_id, utrace_user_id),
  CONSTRAINT "client_users_pkey" PRIMARY KEY (id),
  CONSTRAINT "client_users_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "client_users_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id)
);

ALTER TABLE "public"."client_users"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX client_users_user_idx ON public.client_users USING btree (utrace_user_id);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_users" TO "service_role";

REVOKE ALL ON TABLE "public"."client_users" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_users" TO "postgres";

REVOKE ALL ON TABLE "public"."client_users" FROM "anon", "authenticated";
