CREATE TABLE "public"."telemetry_evidence_items" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "evidence_window_id"    uuid                     NOT NULL,
  "source_record_id"      uuid                     NOT NULL,
  "source_observation_id" uuid,
  "trace_id"              text,
  "span_id"               text,
  "target_id"             text,
  "artifact_id"           uuid,
  "claim_ref"             text                     NOT NULL,
  "supports"              boolean                  NOT NULL,
  "policy_version"        text                     NOT NULL,
  "source_schema_version" text                     NOT NULL,
  "correlation_revision"  bigint                   NOT NULL,
  "watermarks"            jsonb                    NOT NULL,
  "promoted_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_evidence_items_artifact_id_fkey" FOREIGN KEY (artifact_id) REFERENCES public.telemetry_artifacts(id),
  CONSTRAINT "telemetry_evidence_items_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_evidence_items_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_evidence_items_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.telemetry_evidence_windows(id)
);

ALTER TABLE "public"."telemetry_evidence_items"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."telemetry_evidence_items"
  ADD COLUMN "source_class" public.telemetry_source_class NOT NULL;

ALTER TABLE "public"."telemetry_evidence_items"
  ADD CONSTRAINT "telemetry_evidence_items_evidence_window_id_source_class_so_key" UNIQUE (evidence_window_id, source_class, source_record_id, claim_ref);

CREATE INDEX telemetry_evidence_items_window_idx ON public.telemetry_evidence_items USING btree (evidence_window_id, source_class);

CREATE TRIGGER telemetry_evidence_items_materialize_only
  BEFORE DELETE OR UPDATE ON public.telemetry_evidence_items
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_evidence_item_rewrite();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_items" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_evidence_items" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_items" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_evidence_items" FROM "anon", "authenticated";
