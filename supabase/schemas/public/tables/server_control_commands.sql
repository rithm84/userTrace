CREATE TABLE "public"."server_control_commands" (
  "id"                           uuid                     NOT NULL,
  "client_id"                    uuid                     NOT NULL,
  "activation_id"                uuid                     NOT NULL,
  "server_stream_id"             uuid                     NOT NULL,
  "reason"                       text                     NOT NULL,
  "issued_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "delivered_at"                 timestamp with time zone,
  "acknowledged_at"              timestamp with time zone,
  "acknowledged_lifecycle_state" text,
  "schema_version"               smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_control_commands_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "server_control_commands_pkey" PRIMARY KEY (id),
  CONSTRAINT "server_control_commands_server_stream_id_fkey" FOREIGN KEY (server_stream_id) REFERENCES public.server_streams(id),
  CONSTRAINT "server_control_commands_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id)
);

ALTER TABLE "public"."server_control_commands"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."server_control_commands"
  ADD COLUMN "kind" public.server_control_command_kind NOT NULL;

CREATE INDEX server_control_commands_stream_idx ON public.server_control_commands USING btree (server_stream_id, issued_at DESC);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_control_commands" TO "service_role";

REVOKE ALL ON TABLE "public"."server_control_commands" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_control_commands" TO "postgres";

REVOKE ALL ON TABLE "public"."server_control_commands" FROM "anon", "authenticated";
