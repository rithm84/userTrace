CREATE TABLE "public"."browser_observations" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "activation_id"            uuid                     NOT NULL,
  "browser_stream_id"        uuid                     NOT NULL,
  "ingestion_receipt_id"     uuid                     NOT NULL,
  "observation_id"           uuid                     NOT NULL,
  "observation_type"         text                     NOT NULL,
  "runtime_id"               uuid                     NOT NULL,
  "document_attachment_id"   uuid                     NOT NULL,
  "frame_id"                 text                     NOT NULL,
  "source_sequence"          bigint                   NOT NULL,
  "export_sequence"          bigint                   NOT NULL,
  "occurred_at"              timestamp with time zone NOT NULL,
  "monotonic_offset"         double precision         NOT NULL,
  "route_revision"           bigint                   NOT NULL,
  "visibility_state"         text                     NOT NULL,
  "target_id"                text,
  "target_revision"          bigint,
  "trace_id"                 text,
  "span_id"                  text,
  "browser_request_id"       text,
  "payload"                  jsonb                    NOT NULL,
  "policy_version"           text                     NOT NULL,
  "redaction_version"        text                     NOT NULL,
  "source_schema_version"    text                     NOT NULL,
  "canonical_schema_version" text                     NOT NULL,
  "normalization_run_id"     uuid                     NOT NULL,
  "content_sha256"           text                     NOT NULL,
  "received_at"              timestamp with time zone NOT NULL,
  "accepted_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "browser_observations_pkey" PRIMARY KEY (id),
  CONSTRAINT "browser_observations_span_id_format" CHECK (((span_id IS NULL) OR (span_id ~ '^[0-9a-f]{16}$'::text))),
  CONSTRAINT "browser_observations_trace_id_format" CHECK (((trace_id IS NULL) OR (trace_id ~ '^[0-9a-f]{32}$'::text))),
  CONSTRAINT "browser_observations_browser_stream_id_fkey" FOREIGN KEY (browser_stream_id) REFERENCES public.browser_streams(id),
  CONSTRAINT "browser_observations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "browser_observations_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id),
  CONSTRAINT "browser_observations_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."browser_observations"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX browser_observations_identity ON public.browser_observations USING btree (client_id, observation_id);

CREATE INDEX browser_observations_request_idx ON public.browser_observations USING btree (client_id, browser_request_id)
  WHERE (browser_request_id IS NOT NULL);

CREATE INDEX browser_observations_stream_idx ON public.browser_observations USING btree (browser_stream_id, export_sequence);

CREATE INDEX browser_observations_target_idx ON public.browser_observations USING btree (client_id, target_id, occurred_at)
  WHERE (target_id IS NOT NULL);

CREATE INDEX browser_observations_trace_idx ON public.browser_observations USING btree (client_id, trace_id)
  WHERE (trace_id IS NOT NULL);

CREATE TRIGGER browser_observations_immutable
  BEFORE DELETE OR UPDATE ON public.browser_observations
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER browser_observations_notify_voice_sideband
  AFTER INSERT ON public.browser_observations REFERENCING NEW TABLE AS inserted
  FOR EACH STATEMENT
  EXECUTE FUNCTION utrace.notify_voice_sideband_observations();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_observations" TO "service_role";

REVOKE ALL ON TABLE "public"."browser_observations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_observations" TO "postgres";

REVOKE ALL ON TABLE "public"."browser_observations" FROM "anon", "authenticated";
