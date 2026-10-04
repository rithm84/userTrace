CREATE TABLE "public"."results" (
  "id"                 uuid                     NOT NULL,
  "client_id"          uuid                     NOT NULL,
  "command_id"         uuid                     NOT NULL,
  "technical_category" text,
  "payload"            jsonb                    NOT NULL,
  "received_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"     smallint                 NOT NULL DEFAULT 1,
  "created_at"         timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "results_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "results_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id),
  CONSTRAINT "results_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."results"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."results"
  ADD COLUMN "disposition" public.command_disposition;

ALTER TABLE "public"."results"
  ADD COLUMN "effect_state" public.effect_state;

ALTER TABLE "public"."results"
  ADD COLUMN "kind" public.result_kind NOT NULL;

ALTER TABLE "public"."results"
  ADD COLUMN "retry_advice" public.retry_advice;

ALTER TABLE "public"."results"
  ADD CONSTRAINT "results_terminal_effect_state" CHECK (((kind = 'terminal'::public.result_kind) = (effect_state IS NOT NULL)));

ALTER TABLE "public"."results"
  ADD CONSTRAINT "results_terminal_fields" CHECK (((kind = 'terminal'::public.result_kind) = (disposition IS NOT NULL)));

CREATE INDEX results_command_idx ON public.results USING btree (command_id, received_at);

CREATE UNIQUE INDEX results_one_terminal ON public.results USING btree (command_id)
  WHERE (kind = 'terminal'::public.result_kind);

CREATE TRIGGER results_immutable
  BEFORE DELETE OR UPDATE ON public.results
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."results" TO "service_role";

REVOKE ALL ON TABLE "public"."results" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."results" TO "postgres";

REVOKE ALL ON TABLE "public"."results" FROM "anon", "authenticated";
