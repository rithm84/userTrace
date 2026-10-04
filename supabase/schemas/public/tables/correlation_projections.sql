CREATE TABLE "public"."correlation_projections" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "activation_id"            uuid                     NOT NULL,
  "server_activation_id"     uuid,
  "interview_session_id"     uuid                     NOT NULL,
  "trace_id"                 text                     NOT NULL,
  "revision"                 bigint                   NOT NULL DEFAULT 1,
  "browser_stream_id"        uuid,
  "browser_request_id"       text,
  "required_components"      jsonb                    NOT NULL,
  "observed_components"      jsonb                    NOT NULL,
  "gaps"                     jsonb                    NOT NULL,
  "conflicts"                jsonb                    NOT NULL,
  "first_watermark_at"       timestamp with time zone,
  "last_watermark_at"        timestamp with time zone,
  "browser_accepted_through" bigint                   NOT NULL DEFAULT 0,
  "server_accepted_through"  bigint                   NOT NULL DEFAULT 0,
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "correlation_projections_browser_stream_id_fkey" FOREIGN KEY (browser_stream_id) REFERENCES public.browser_streams(id),
  CONSTRAINT "correlation_projections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "correlation_projections_pkey" PRIMARY KEY (id),
  CONSTRAINT "correlation_projections_trace_id_format" CHECK ((trace_id ~ '^[0-9a-f]{32}$'::text)),
  CONSTRAINT "correlation_projections_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "correlation_projections_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id),
  CONSTRAINT "correlation_projections_server_activation_id_fkey" FOREIGN KEY (server_activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."correlation_projections"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."correlation_projections"
  ADD COLUMN "status" public.correlation_status NOT NULL;

CREATE UNIQUE INDEX correlation_projections_identity ON public.correlation_projections USING btree (client_id, activation_id, trace_id);

CREATE INDEX correlation_projections_session_idx ON public.correlation_projections USING btree (interview_session_id, status);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."correlation_projections" TO "service_role";

REVOKE ALL ON TABLE "public"."correlation_projections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."correlation_projections" TO "postgres";

REVOKE ALL ON TABLE "public"."correlation_projections" FROM "anon", "authenticated";
