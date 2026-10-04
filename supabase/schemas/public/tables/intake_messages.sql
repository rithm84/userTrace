CREATE TABLE "public"."intake_messages" (
  "id"                           uuid                     NOT NULL,
  "client_id"                    uuid                     NOT NULL,
  "intake_batch_id"              uuid                     NOT NULL,
  "provider"                     text                     NOT NULL,
  "channel_ref"                  text                     NOT NULL,
  "provider_message_id"          text                     NOT NULL,
  "author_provider_identity"     text                     NOT NULL,
  "author_is_bot"                boolean                  NOT NULL,
  "ordinal"                      integer                  NOT NULL,
  "content"                      text                     NOT NULL,
  "reply_to_provider_message_id" text,
  "thread_ref"                   text,
  "occurred_at"                  timestamp with time zone NOT NULL,
  "edited_at"                    timestamp with time zone,
  "schema_version"               smallint                 NOT NULL DEFAULT 1,
  "created_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_messages_batch_fk" FOREIGN KEY (intake_batch_id) REFERENCES public.intake_batches(id),
  CONSTRAINT "intake_messages_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "intake_messages_client_id_provider_provider_message_id_key" UNIQUE (client_id, PROVIDER, provider_message_id),
  CONSTRAINT "intake_messages_ordinal_positive" CHECK ((ordinal > 0)),
  CONSTRAINT "intake_messages_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."intake_messages"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX intake_messages_batch_idx ON public.intake_messages USING btree (intake_batch_id, ordinal);

CREATE TRIGGER intake_messages_immutable
  BEFORE DELETE OR UPDATE ON public.intake_messages
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_messages" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_messages" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_messages" TO "postgres";

REVOKE ALL ON TABLE "public"."intake_messages" FROM "anon", "authenticated";
