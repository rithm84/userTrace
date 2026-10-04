CREATE TABLE "public"."preview_runtime_allocations" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "product_signal_id"               uuid,
  "revision"                        bigint                   NOT NULL DEFAULT 0,
  "machine_version"                 smallint                 NOT NULL DEFAULT 1,
  "interview_session_id"            uuid,
  "preview_version_id"              uuid,
  "seed_manifest_digest"            text,
  "runtime_ref"                     text,
  "origin_host"                     text,
  "released_at"                     timestamp with time zone,
  "terminated_at"                   timestamp with time zone,
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "preview_runtime_template_id"     uuid,
  "pinned_configuration_release_id" uuid,
  "image_digest"                    text,
  "recipe_digest"                   text,
  "fixture_clock"                   timestamp with time zone,
  "authenticated_identity"          text,
  "sdk_versions"                    jsonb,
  "application_port"                integer,
  "route_revision"                  integer,
  "handoff_secret"                  text,
  "claimed_at"                      timestamp with time zone,
  "evidence"                        jsonb,
  "fencing_token"                   bigint                   NOT NULL,
  "route_context_signing_secret"    text,
  "preview_entry_token"             text,
  "runtime_expires_at"              timestamp with time zone,
  CONSTRAINT "preview_runtime_allocations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_runtime_allocations_origin_host_key" UNIQUE (origin_host),
  CONSTRAINT "preview_runtime_allocations_pinned_configuration_release_i_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id),
  CONSTRAINT "preview_runtime_allocations_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_runtime_allocations_preview_runtime_template_id_fkey" FOREIGN KEY (preview_runtime_template_id) REFERENCES public.preview_runtime_templates(id),
  CONSTRAINT "preview_runtime_allocations_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."preview_runtime_allocations"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "allocation_kind" public.preview_allocation_kind NOT NULL;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "pool_role" public.preview_pool_role NOT NULL;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "state" public.preview_runtime_allocation_state NOT NULL;

CREATE UNIQUE INDEX preview_runtime_allocations_one_base_per_session ON public.preview_runtime_allocations USING btree (interview_session_id)
  WHERE ((allocation_kind = 'base'::public.preview_allocation_kind) AND (interview_session_id IS NOT NULL));

CREATE UNIQUE INDEX preview_runtime_allocations_one_live_candidate ON public.preview_runtime_allocations USING btree (preview_version_id)
  WHERE ((allocation_kind = 'candidate'::public.preview_allocation_kind) AND (preview_version_id IS NOT NULL) AND (state <> 'terminated'::public.preview_runtime_allocation_state));

CREATE INDEX preview_runtime_allocations_pool_idx ON public.preview_runtime_allocations USING btree (preview_runtime_template_id, pool_role, state);

CREATE INDEX preview_runtime_allocations_signal_idx ON public.preview_runtime_allocations USING btree (product_signal_id, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_allocations" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_runtime_allocations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_allocations" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_runtime_allocations" FROM "anon", "authenticated";
