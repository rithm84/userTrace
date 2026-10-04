CREATE TABLE "public"."preview_runtime_templates" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "template_id"                     text                     NOT NULL,
  "template_revision"               integer                  NOT NULL,
  "snapshot_id"                     text                     NOT NULL,
  "image_digest"                    text                     NOT NULL,
  "recipe_digest"                   text                     NOT NULL,
  "seed_manifest_digest"            text                     NOT NULL,
  "seed_manifest"                   jsonb                    NOT NULL,
  "fixture_clock"                   timestamp with time zone NOT NULL,
  "application_port"                integer                  NOT NULL,
  "authenticated_identity"          text                     NOT NULL,
  "sdk_versions"                    jsonb                    NOT NULL,
  "warm_pool_target"                integer                  NOT NULL,
  "retired_at"                      timestamp with time zone,
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "preview_runtime_templates_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_runtime_templates_client_id_template_id_template_re_key" UNIQUE (client_id, template_id, template_revision),
  CONSTRAINT "preview_runtime_templates_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id),
  CONSTRAINT "preview_runtime_templates_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_runtime_templates_pool_target_nonnegative" CHECK ((warm_pool_target >= 0)),
  CONSTRAINT "preview_runtime_templates_port_range" CHECK (((application_port >= 1) AND (application_port <= 65535)))
);

ALTER TABLE "public"."preview_runtime_templates"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX preview_runtime_templates_one_current ON public.preview_runtime_templates USING btree (client_id)
  WHERE (retired_at IS NULL);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_templates" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_runtime_templates" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_templates" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_runtime_templates" FROM "anon", "authenticated";
