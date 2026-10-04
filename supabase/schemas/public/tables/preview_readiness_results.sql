CREATE TABLE "public"."preview_readiness_results" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "preview_route_id"              uuid,
  "preview_version_id"            uuid,
  "route_revision"                integer,
  "probe"                         text                     NOT NULL,
  "detail"                        jsonb                    NOT NULL,
  "observed_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "preview_readiness_results_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_readiness_results_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_readiness_results_preview_route_id_fkey" FOREIGN KEY (preview_route_id) REFERENCES public.preview_routes(id),
  CONSTRAINT "preview_readiness_results_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id),
  CONSTRAINT "preview_readiness_results_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id)
);

ALTER TABLE "public"."preview_readiness_results"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."preview_readiness_results"
  ADD COLUMN "outcome" public.preview_readiness_outcome NOT NULL;

CREATE INDEX preview_readiness_results_allocation_idx ON public.preview_readiness_results USING btree (preview_runtime_allocation_id, observed_at DESC);

CREATE TRIGGER preview_readiness_results_immutable
  BEFORE DELETE OR UPDATE ON public.preview_readiness_results
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_readiness_results" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_readiness_results" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_readiness_results" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_readiness_results" FROM "anon", "authenticated";
