CREATE TABLE "public"."events" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "stream_id"      uuid                     NOT NULL,
  "sequence"       bigint                   NOT NULL,
  "event_type"     text                     NOT NULL,
  "payload"        jsonb                    NOT NULL,
  "actor_ref"      text,
  "causation_id"   uuid,
  "correlation_id" uuid,
  "occurred_at"    timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "events_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "events_pkey" PRIMARY KEY (id),
  CONSTRAINT "events_sequence_positive" CHECK ((sequence > 0))
);

ALTER TABLE "public"."events"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."events"
  ADD COLUMN "actor_kind" public.actor_kind NOT NULL;

ALTER TABLE "public"."events"
  ADD COLUMN "stream_kind" public.aggregate_kind NOT NULL;

ALTER TABLE "public"."events"
  ADD CONSTRAINT "events_stream_kind_stream_id_sequence_key" UNIQUE (stream_kind, stream_id, SEQUENCE);

CREATE INDEX events_client_stream_idx ON public.events USING btree (client_id, stream_kind, stream_id, SEQUENCE);

CREATE TRIGGER events_immutable
  BEFORE DELETE OR UPDATE ON public.events
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."events" TO "service_role";

REVOKE ALL ON TABLE "public"."events" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."events" TO "postgres";

REVOKE ALL ON TABLE "public"."events" FROM "anon", "authenticated";
