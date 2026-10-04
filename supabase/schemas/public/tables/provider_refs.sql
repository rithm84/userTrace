CREATE TABLE "public"."provider_refs" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "provider"             text                     NOT NULL,
  "installation_ref"     text                     NOT NULL,
  "resource_type"        text                     NOT NULL,
  "provider_resource_id" text                     NOT NULL,
  "owner_id"             uuid                     NOT NULL,
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "provider_refs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "provider_refs_client_id_provider_installation_ref_resource__key" UNIQUE (client_id, PROVIDER, installation_ref, resource_type, provider_resource_id),
  CONSTRAINT "provider_refs_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."provider_refs"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."provider_refs"
  ADD COLUMN "owner_kind" public.aggregate_kind NOT NULL;

CREATE INDEX provider_refs_owner_idx ON public.provider_refs USING btree (owner_kind, owner_id);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."provider_refs" TO "service_role";

REVOKE ALL ON TABLE "public"."provider_refs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."provider_refs" TO "postgres";

REVOKE ALL ON TABLE "public"."provider_refs" FROM "anon", "authenticated";
