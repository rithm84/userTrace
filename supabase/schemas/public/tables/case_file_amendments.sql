CREATE TABLE "public"."case_file_amendments" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "case_file_id"   uuid                     NOT NULL,
  "ordinal"        integer                  NOT NULL,
  "reason"         text                     NOT NULL,
  "body"           jsonb                    NOT NULL,
  "actor_ref"      text,
  "recorded_at"    timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "case_file_amendments_case_file_id_ordinal_key" UNIQUE (case_file_id, ordinal),
  CONSTRAINT "case_file_amendments_ordinal_positive" CHECK ((ordinal >= 1)),
  CONSTRAINT "case_file_amendments_pkey" PRIMARY KEY (id),
  CONSTRAINT "case_file_amendments_case_file_id_fkey" FOREIGN KEY (case_file_id) REFERENCES public.case_files(id),
  CONSTRAINT "case_file_amendments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id)
);

ALTER TABLE "public"."case_file_amendments"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."case_file_amendments"
  ADD COLUMN "actor_kind" public.actor_kind NOT NULL;

CREATE INDEX case_file_amendments_case_file_idx ON public.case_file_amendments USING btree (case_file_id, ordinal);

CREATE TRIGGER case_file_amendments_immutable
  BEFORE DELETE OR UPDATE ON public.case_file_amendments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "case_file_amendments_member_select" ON "public"."case_file_amendments"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_file_amendments" TO "service_role";

REVOKE ALL ON TABLE "public"."case_file_amendments" FROM "authenticated";

GRANT SELECT ON TABLE "public"."case_file_amendments" TO "authenticated";

REVOKE ALL ON TABLE "public"."case_file_amendments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_file_amendments" TO "postgres";

REVOKE ALL ON TABLE "public"."case_file_amendments" FROM "anon";
