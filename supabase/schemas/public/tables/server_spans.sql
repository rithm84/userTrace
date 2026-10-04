CREATE TABLE "public"."server_spans" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "activation_id"               uuid                     NOT NULL,
  "server_stream_id"            uuid                     NOT NULL,
  "ingestion_receipt_id"        uuid                     NOT NULL,
  "trace_id"                    text                     NOT NULL,
  "span_id"                     text                     NOT NULL,
  "parent_span_id"              text,
  "span_name"                   text                     NOT NULL,
  "span_kind"                   text                     NOT NULL,
  "start_time_unix_nano"        bigint                   NOT NULL,
  "end_time_unix_nano"          bigint                   NOT NULL,
  "status_code"                 text                     NOT NULL,
  "scope_name"                  text                     NOT NULL,
  "scope_version"               text,
  "service_name"                text                     NOT NULL,
  "service_namespace"           text                     NOT NULL,
  "service_version"             text                     NOT NULL,
  "deployment_environment_name" text                     NOT NULL,
  "attributes"                  jsonb                    NOT NULL,
  "links"                       jsonb                    NOT NULL,
  "span_events"                 jsonb                    NOT NULL,
  "registered_operation"        text,
  "http_route"                  text,
  "http_status_code"            integer,
  "source_schema_version"       text                     NOT NULL,
  "canonical_schema_version"    text                     NOT NULL,
  "semantic_convention_version" text                     NOT NULL,
  "policy_version"              text                     NOT NULL,
  "normalization_run_id"        uuid                     NOT NULL,
  "content_sha256"              text                     NOT NULL,
  "received_at"                 timestamp with time zone NOT NULL,
  "accepted_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_spans_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "server_spans_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id),
  CONSTRAINT "server_spans_interval_ordered" CHECK ((end_time_unix_nano >= start_time_unix_nano)),
  CONSTRAINT "server_spans_parent_span_id_format" CHECK (((parent_span_id IS NULL) OR (parent_span_id ~ '^[0-9a-f]{16}$'::text))),
  CONSTRAINT "server_spans_pkey" PRIMARY KEY (id),
  CONSTRAINT "server_spans_span_id_format" CHECK ((span_id ~ '^[0-9a-f]{16}$'::text)),
  CONSTRAINT "server_spans_trace_id_format" CHECK ((trace_id ~ '^[0-9a-f]{32}$'::text)),
  CONSTRAINT "server_spans_server_stream_id_fkey" FOREIGN KEY (server_stream_id) REFERENCES public.server_streams(id),
  CONSTRAINT "server_spans_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."server_spans"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX server_spans_identity ON public.server_spans USING btree (client_id, activation_id, trace_id, span_id);

CREATE INDEX server_spans_operation_idx ON public.server_spans USING btree (client_id, registered_operation, accepted_at)
  WHERE (registered_operation IS NOT NULL);

CREATE INDEX server_spans_parent_idx ON public.server_spans USING btree (client_id, trace_id, parent_span_id)
  WHERE (parent_span_id IS NOT NULL);

CREATE INDEX server_spans_trace_idx ON public.server_spans USING btree (client_id, trace_id);

CREATE TRIGGER server_spans_immutable
  BEFORE DELETE OR UPDATE ON public.server_spans
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_spans" TO "service_role";

REVOKE ALL ON TABLE "public"."server_spans" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_spans" TO "postgres";

REVOKE ALL ON TABLE "public"."server_spans" FROM "anon", "authenticated";
