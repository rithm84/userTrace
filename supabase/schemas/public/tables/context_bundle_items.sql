CREATE TABLE "public"."context_bundle_items" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "context_bundle_id" uuid                     NOT NULL,
  "ordinal"           integer                  NOT NULL,
  "source_kind"       text                     NOT NULL,
  "source_id"         text                     NOT NULL,
  "source_revision"   text                     NOT NULL,
  "included"          boolean                  NOT NULL,
  "omission_reason"   text,
  "weight"            integer                  NOT NULL,
  "body"              jsonb                    NOT NULL,
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "context_bundle_items_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "context_bundle_items_context_bundle_id_ordinal_key" UNIQUE (context_bundle_id, ordinal),
  CONSTRAINT "context_bundle_items_omission_reason" CHECK ((included = (omission_reason IS NULL))),
  CONSTRAINT "context_bundle_items_pkey" PRIMARY KEY (id),
  CONSTRAINT "context_bundle_items_weight_positive" CHECK ((weight > 0)),
  CONSTRAINT "context_bundle_items_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id)
);

ALTER TABLE "public"."context_bundle_items"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."context_bundle_items"
  ADD COLUMN "section" public.context_section NOT NULL;

CREATE INDEX context_bundle_items_source_idx ON public.context_bundle_items USING btree (source_kind, source_id);

CREATE TRIGGER context_bundle_items_immutable
  BEFORE DELETE OR UPDATE ON public.context_bundle_items
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_items" TO "service_role";

REVOKE ALL ON TABLE "public"."context_bundle_items" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_items" TO "postgres";

REVOKE ALL ON TABLE "public"."context_bundle_items" FROM "anon", "authenticated";
