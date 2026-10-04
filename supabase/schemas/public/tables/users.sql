CREATE TABLE "public"."users" (
  "id"                  uuid                     NOT NULL,
  "auth_user_id"        uuid,
  "display_name"        text,
  "do_not_contact"      boolean                  NOT NULL DEFAULT false,
  "participation_count" integer                  NOT NULL DEFAULT 0,
  "revision"            bigint                   NOT NULL DEFAULT 0,
  "schema_version"      smallint                 NOT NULL DEFAULT 1,
  "created_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"          timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "users_auth_user_id_fkey" FOREIGN KEY (auth_user_id) REFERENCES auth.users(id),
  CONSTRAINT "users_auth_user_id_key" UNIQUE (auth_user_id),
  CONSTRAINT "users_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."users"
  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_self_select" ON "public"."users"
  FOR SELECT
  TO "authenticated"
  USING ((auth_user_id = ( SELECT auth.uid() AS uid)));

CREATE POLICY "users_self_update" ON "public"."users"
  FOR UPDATE
  TO "authenticated"
  USING ((auth_user_id = ( SELECT auth.uid() AS uid)))
  WITH CHECK ((auth_user_id = ( SELECT auth.uid() AS uid)));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."users" TO "service_role";

REVOKE ALL ON TABLE "public"."users" FROM "authenticated";

GRANT SELECT, UPDATE ON TABLE "public"."users" TO "authenticated";

REVOKE ALL ON TABLE "public"."users" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."users" TO "postgres";

REVOKE ALL ON TABLE "public"."users" FROM "anon";
