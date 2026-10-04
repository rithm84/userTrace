CREATE TABLE "public"."context_bundle_invalidations" (
  "context_bundle_id"     uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "reason"                text                     NOT NULL,
  "caused_by_evidence_id" uuid,
  "invalidated_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "context_bundle_invalidations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "context_bundle_invalidations_pkey" PRIMARY KEY (context_bundle_id),
  CONSTRAINT "context_bundle_invalidations_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id),
  CONSTRAINT "context_bundle_invalidations_caused_by_evidence_id_fkey" FOREIGN KEY (caused_by_evidence_id) REFERENCES public.evidence_records(id),
  CONSTRAINT "context_bundle_invalidations_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."context_bundle_invalidations"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX context_bundle_invalidations_signal_idx ON public.context_bundle_invalidations USING btree (product_signal_id, invalidated_at DESC);

CREATE TRIGGER context_bundle_invalidations_immutable
  BEFORE DELETE OR UPDATE ON public.context_bundle_invalidations
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_invalidations" TO "service_role";

REVOKE ALL ON TABLE "public"."context_bundle_invalidations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_invalidations" TO "postgres";

REVOKE ALL ON TABLE "public"."context_bundle_invalidations" FROM "anon", "authenticated";
