CREATE TABLE "public"."commands" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "command_type"          text                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "target_id"             uuid                     NOT NULL,
  "payload"               jsonb                    NOT NULL,
  "declared_revisions"    jsonb                    NOT NULL,
  "cancellation_scope_id" uuid,
  "provider_ref"          text,
  "queued_at"             timestamp with time zone,
  "dispatching_at"        timestamp with time zone,
  "dispatched_at"         timestamp with time zone,
  "acknowledged_at"       timestamp with time zone,
  "reconciling_at"        timestamp with time zone,
  "terminal_at"           timestamp with time zone,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "commands_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "commands_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."commands"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."commands"
  ADD COLUMN "target_kind" public.aggregate_kind NOT NULL;

ALTER TABLE "public"."commands"
  ADD COLUMN "state" public.command_state NOT NULL;

CREATE INDEX commands_cancellation_scope_idx ON public.commands USING btree (cancellation_scope_id)
  WHERE (state <> 'terminal'::public.command_state);

CREATE INDEX commands_target_idx ON public.commands USING btree (target_kind, target_id);

CREATE TRIGGER commands_notify_voice_sideband
  AFTER INSERT ON public.commands
  FOR EACH ROW
  WHEN ((new.command_type = 'voice.set_input_muted'::text))
  EXECUTE FUNCTION utrace.notify_voice_sideband('target_id');

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."commands" TO "service_role";

REVOKE ALL ON TABLE "public"."commands" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."commands" TO "postgres";

REVOKE ALL ON TABLE "public"."commands" FROM "anon", "authenticated";
