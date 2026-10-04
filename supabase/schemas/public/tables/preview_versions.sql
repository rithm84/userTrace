CREATE TABLE "public"."preview_versions" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "product_signal_id"                uuid                     NOT NULL,
  "implementation_attempt_id"        uuid                     NOT NULL,
  "revision"                         bigint                   NOT NULL DEFAULT 0,
  "machine_version"                  smallint                 NOT NULL DEFAULT 1,
  "source_commit"                    text                     NOT NULL,
  "source_digest"                    text                     NOT NULL,
  "build_digest"                     text                     NOT NULL,
  "base_revision"                    text                     NOT NULL,
  "seed_manifest_digest"             text,
  "ready_at"                         timestamp with time zone,
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  "created_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "source_manifest"                  jsonb,
  "patch_digest"                     text,
  "patch_text"                       text,
  "build_recipe"                     jsonb,
  "migrations"                       jsonb,
  "artifact_recorded_at"             timestamp with time zone,
  "readiness_recorded_at"            timestamp with time zone,
  "failure_reason"                   text,
  "artifact_manifest_digest"         text,
  "check_summary"                    jsonb,
  "superseded_by_preview_version_id" uuid,
  CONSTRAINT "preview_versions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_versions_implementation_attempt_id_key" UNIQUE (implementation_attempt_id),
  CONSTRAINT "preview_versions_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_versions_superseded_by_preview_version_id_fkey" FOREIGN KEY (superseded_by_preview_version_id) REFERENCES public.preview_versions(id),
  CONSTRAINT "preview_versions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."preview_versions"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."preview_versions"
  ADD COLUMN "state" public.preview_version_state NOT NULL;

CREATE INDEX preview_versions_signal_idx ON public.preview_versions USING btree (product_signal_id, state);

CREATE POLICY "preview_versions_member_select" ON "public"."preview_versions"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_versions" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_versions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."preview_versions" TO "authenticated";

REVOKE ALL ON TABLE "public"."preview_versions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_versions" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_versions" FROM "anon";
