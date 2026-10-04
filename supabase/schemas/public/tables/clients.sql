CREATE TABLE "public"."clients" (
  "id"             uuid                     NOT NULL,
  "name"           text                     NOT NULL,
  "product_name"   text                     NOT NULL,
  "revision"       bigint                   NOT NULL DEFAULT 0,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "clients_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."clients"
  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "clients_member_select" ON "public"."clients"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(id));

CREATE POLICY "clients_owner_update" ON "public"."clients"
  FOR UPDATE
  TO "authenticated"
  USING (utrace.is_client_owner(id))
  WITH CHECK (utrace.is_client_owner(id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."clients" TO "service_role";

REVOKE ALL ON TABLE "public"."clients" FROM "authenticated";

GRANT SELECT, UPDATE ON TABLE "public"."clients" TO "authenticated";

REVOKE ALL ON TABLE "public"."clients" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."clients" TO "postgres";

REVOKE ALL ON TABLE "public"."clients" FROM "anon";
