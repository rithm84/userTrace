CREATE TABLE "public"."preview_environments" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "preview_runtime_template_id"     uuid                     NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "preview_environments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_environments_client_id_preview_runtime_template_id_key" UNIQUE (client_id, preview_runtime_template_id),
  CONSTRAINT "preview_environments_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id),
  CONSTRAINT "preview_environments_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_environments_preview_runtime_template_id_fkey" FOREIGN KEY (preview_runtime_template_id) REFERENCES public.preview_runtime_templates(id)
);

ALTER TABLE "public"."preview_environments"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX preview_environments_release_idx ON public.preview_environments USING btree (pinned_configuration_release_id);

CREATE TRIGGER preview_environments_immutable
  BEFORE DELETE OR UPDATE ON public.preview_environments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_environments" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_environments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_environments" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_environments" FROM "anon", "authenticated";
