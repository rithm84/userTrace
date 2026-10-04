CREATE TABLE "public"."server_streams" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "activation_id"                   uuid                     NOT NULL,
  "service_instance_id"             uuid                     NOT NULL,
  "service_key"                     text                     NOT NULL,
  "revision"                        bigint                   NOT NULL DEFAULT 0,
  "accepted_through_batch_sequence" bigint                   NOT NULL DEFAULT 0,
  "highest_received_batch_sequence" bigint                   NOT NULL DEFAULT 0,
  "clean"                           boolean,
  "opened_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_streams_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "server_streams_pkey" PRIMARY KEY (id),
  CONSTRAINT "server_streams_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."server_streams"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."server_streams"
  ADD COLUMN "state" public.telemetry_stream_state NOT NULL DEFAULT 'open'::public.telemetry_stream_state;

CREATE INDEX server_streams_activation_idx ON public.server_streams USING btree (activation_id, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_streams" TO "service_role";

REVOKE ALL ON TABLE "public"."server_streams" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_streams" TO "postgres";

REVOKE ALL ON TABLE "public"."server_streams" FROM "anon", "authenticated";
