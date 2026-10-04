CREATE TABLE "public"."audit_records" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "subject_id"                      uuid                     NOT NULL,
  "action"                          text                     NOT NULL,
  "outcome"                         text                     NOT NULL,
  "reason_code"                     text,
  "actor_ref"                       text,
  "lifecycle_revision"              bigint,
  "pinned_configuration_release_id" uuid,
  "detail"                          jsonb                    NOT NULL,
  "recorded_at"                     timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "audit_records_pkey" PRIMARY KEY (id),
  CONSTRAINT "audit_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "audit_records_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id)
);

ALTER TABLE "public"."audit_records"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."audit_records"
  ADD COLUMN "actor_kind" public.actor_kind NOT NULL;

ALTER TABLE "public"."audit_records"
  ADD COLUMN "subject_kind" public.aggregate_kind NOT NULL;

CREATE INDEX audit_records_client_idx ON public.audit_records USING btree (client_id, recorded_at);

CREATE INDEX audit_records_subject_idx ON public.audit_records USING btree (subject_kind, subject_id, recorded_at);

CREATE TRIGGER audit_records_immutable
  BEFORE DELETE OR UPDATE ON public.audit_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."audit_records" TO "service_role";

REVOKE ALL ON TABLE "public"."audit_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."audit_records" TO "postgres";

REVOKE ALL ON TABLE "public"."audit_records" FROM "anon", "authenticated";
