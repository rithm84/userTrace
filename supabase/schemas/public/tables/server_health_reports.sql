CREATE TABLE "public"."server_health_reports" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "activation_id"               uuid                     NOT NULL,
  "server_stream_id"            uuid                     NOT NULL,
  "reported_at"                 timestamp with time zone NOT NULL,
  "received_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "lifecycle_state"             text                     NOT NULL,
  "sdk_version"                 text                     NOT NULL,
  "contract_major"              integer                  NOT NULL,
  "node_version"                text                     NOT NULL,
  "service_key"                 text                     NOT NULL,
  "service_instance_id"         uuid                     NOT NULL,
  "manifest_version"            integer,
  "policy_version"              text,
  "utrace_schema_version"       text,
  "semantic_convention_version" text,
  "credential_expires_at"       timestamp with time zone,
  "capabilities"                jsonb                    NOT NULL,
  "adapters"                    jsonb                    NOT NULL,
  "buffer"                      jsonb                    NOT NULL,
  "probe_result"                jsonb                    NOT NULL,
  "failures"                    jsonb                    NOT NULL,
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_health_reports_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "server_health_reports_pkey" PRIMARY KEY (id),
  CONSTRAINT "server_health_reports_server_stream_id_fkey" FOREIGN KEY (server_stream_id) REFERENCES public.server_streams(id),
  CONSTRAINT "server_health_reports_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."server_health_reports"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX server_health_reports_activation_idx ON public.server_health_reports USING btree (activation_id, reported_at DESC);

CREATE INDEX server_health_reports_stream_idx ON public.server_health_reports USING btree (server_stream_id, reported_at DESC);

CREATE TRIGGER server_health_reports_immutable
  BEFORE DELETE OR UPDATE ON public.server_health_reports
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_health_reports" TO "service_role";

REVOKE ALL ON TABLE "public"."server_health_reports" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_health_reports" TO "postgres";

REVOKE ALL ON TABLE "public"."server_health_reports" FROM "anon", "authenticated";
