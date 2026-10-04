CREATE TABLE "public"."code_investigation_findings" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "product_signal_id" uuid                     NOT NULL,
  "investigation_id"  uuid                     NOT NULL,
  "repository_ref"    text                     NOT NULL,
  "base_revision"     text                     NOT NULL,
  "path"              text                     NOT NULL,
  "first_line"        integer                  NOT NULL,
  "last_line"         integer                  NOT NULL,
  "excerpt_digest"    text                     NOT NULL,
  "note"              text                     NOT NULL,
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "code_investigation_findings_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "code_investigation_findings_line_range" CHECK (((first_line >= 1) AND (last_line >= first_line))),
  CONSTRAINT "code_investigation_findings_pkey" PRIMARY KEY (id),
  CONSTRAINT "code_investigation_findings_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."code_investigation_findings"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX code_investigation_findings_investigation_idx ON public.code_investigation_findings USING btree (investigation_id);

CREATE INDEX code_investigation_findings_signal_idx ON public.code_investigation_findings USING btree (product_signal_id, path);

CREATE TRIGGER code_investigation_findings_immutable
  BEFORE DELETE OR UPDATE ON public.code_investigation_findings
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."code_investigation_findings" TO "service_role";

REVOKE ALL ON TABLE "public"."code_investigation_findings" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."code_investigation_findings" TO "postgres";

REVOKE ALL ON TABLE "public"."code_investigation_findings" FROM "anon", "authenticated";
