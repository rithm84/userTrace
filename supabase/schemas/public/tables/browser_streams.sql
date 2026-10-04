CREATE TABLE "public"."browser_streams" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "activation_id"                    uuid                     NOT NULL,
  "runtime_id"                       uuid                     NOT NULL,
  "document_attachment_id"           uuid                     NOT NULL,
  "revision"                         bigint                   NOT NULL DEFAULT 0,
  "accepted_through_export_sequence" bigint                   NOT NULL DEFAULT 0,
  "highest_received_export_sequence" bigint                   NOT NULL DEFAULT 0,
  "terminal_export_sequence"         bigint,
  "end_reason"                       text,
  "opened_at"                        timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "browser_streams_pkey" PRIMARY KEY (id),
  CONSTRAINT "browser_streams_watermarks_ordered" CHECK ((accepted_through_export_sequence <= highest_received_export_sequence)),
  CONSTRAINT "browser_streams_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "browser_streams_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."browser_streams"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."browser_streams"
  ADD COLUMN "state" public.telemetry_stream_state NOT NULL DEFAULT 'open'::public.telemetry_stream_state;

CREATE INDEX browser_streams_activation_idx ON public.browser_streams USING btree (activation_id, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_streams" TO "service_role";

REVOKE ALL ON TABLE "public"."browser_streams" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_streams" TO "postgres";

REVOKE ALL ON TABLE "public"."browser_streams" FROM "anon", "authenticated";
