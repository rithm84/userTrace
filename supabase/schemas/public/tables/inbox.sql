CREATE TABLE "public"."inbox" (
  "message_id"     uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "source"         text                     NOT NULL,
  "message_kind"   text                     NOT NULL,
  "command_id"     uuid,
  "payload"        jsonb                    NOT NULL,
  "received_at"    timestamp with time zone NOT NULL DEFAULT now(),
  "processed_at"   timestamp with time zone,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "inbox_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "inbox_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id),
  CONSTRAINT "inbox_pkey" PRIMARY KEY (message_id)
);

ALTER TABLE "public"."inbox"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX inbox_one_terminal_per_command ON public.inbox USING btree (command_id)
  WHERE ((message_kind = 'terminal_result'::text) AND (command_id IS NOT NULL));

CREATE INDEX inbox_unprocessed_idx ON public.inbox USING btree (received_at)
  WHERE (processed_at IS NULL);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbox" TO "service_role";

REVOKE ALL ON TABLE "public"."inbox" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbox" TO "postgres";

REVOKE ALL ON TABLE "public"."inbox" FROM "anon", "authenticated";
