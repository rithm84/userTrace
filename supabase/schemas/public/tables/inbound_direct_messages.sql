CREATE TABLE "public"."inbound_direct_messages" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid,
  "provider"                 text                     NOT NULL,
  "provider_message_id"      text                     NOT NULL,
  "sender_provider_identity" text                     NOT NULL,
  "content"                  text                     NOT NULL,
  "route_kind"               text                     NOT NULL,
  "outreach_attempt_id"      uuid,
  "interview_session_id"     uuid,
  "occurred_at"              timestamp with time zone NOT NULL,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "inbound_direct_messages_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "inbound_direct_messages_pkey" PRIMARY KEY (id),
  CONSTRAINT "inbound_direct_messages_provider_provider_message_id_key" UNIQUE (PROVIDER, provider_message_id),
  CONSTRAINT "inbound_direct_messages_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "inbound_direct_messages_outreach_attempt_id_fkey" FOREIGN KEY (outreach_attempt_id) REFERENCES public.outreach_attempts(id)
);

ALTER TABLE "public"."inbound_direct_messages"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX inbound_direct_messages_sender_idx ON public.inbound_direct_messages USING btree (PROVIDER, sender_provider_identity, occurred_at);

CREATE TRIGGER inbound_direct_messages_immutable
  BEFORE DELETE OR UPDATE ON public.inbound_direct_messages
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbound_direct_messages" TO "service_role";

REVOKE ALL ON TABLE "public"."inbound_direct_messages" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbound_direct_messages" TO "postgres";

REVOKE ALL ON TABLE "public"."inbound_direct_messages" FROM "anon", "authenticated";
