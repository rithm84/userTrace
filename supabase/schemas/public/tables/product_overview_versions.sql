CREATE TABLE "public"."product_overview_versions" (
  "id"              uuid                     NOT NULL,
  "client_id"       uuid                     NOT NULL,
  "version_number"  bigint                   NOT NULL,
  "revision"        bigint                   NOT NULL DEFAULT 0,
  "machine_version" smallint                 NOT NULL DEFAULT 1,
  "content_digest"  text                     NOT NULL,
  "overview"        jsonb                    NOT NULL,
  "sources"         jsonb                    NOT NULL,
  "activated_at"    timestamp with time zone,
  "superseded_at"   timestamp with time zone,
  "schema_version"  smallint                 NOT NULL DEFAULT 1,
  "created_at"      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "product_overview_versions_client_id_content_digest_key" UNIQUE (client_id, content_digest),
  CONSTRAINT "product_overview_versions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "product_overview_versions_client_id_version_number_key" UNIQUE (client_id, version_number),
  CONSTRAINT "product_overview_versions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."product_overview_versions"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."product_overview_versions"
  ADD COLUMN "state" public.product_overview_state NOT NULL;

CREATE UNIQUE INDEX product_overview_versions_one_active ON public.product_overview_versions USING btree (client_id)
  WHERE (state = 'active'::public.product_overview_state);

CREATE POLICY "product_overview_versions_member_select" ON "public"."product_overview_versions"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_overview_versions" TO "service_role";

REVOKE ALL ON TABLE "public"."product_overview_versions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."product_overview_versions" TO "authenticated";

REVOKE ALL ON TABLE "public"."product_overview_versions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_overview_versions" TO "postgres";

REVOKE ALL ON TABLE "public"."product_overview_versions" FROM "anon";
