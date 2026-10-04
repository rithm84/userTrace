CREATE TABLE "public"."implementation_scope_checks" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "implementation_attempt_id" uuid                     NOT NULL,
  "cycle_index"               integer                  NOT NULL,
  "passed"                    boolean                  NOT NULL,
  "scope_digest"              text                     NOT NULL,
  "changed_paths"             text[]                   NOT NULL,
  "violations"                jsonb                    NOT NULL,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "implementation_scope_checks_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "implementation_scope_checks_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "implementation_scope_checks_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."implementation_scope_checks"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX implementation_scope_checks_attempt_idx ON public.implementation_scope_checks USING btree (implementation_attempt_id, cycle_index);

CREATE TRIGGER implementation_scope_checks_immutable
  BEFORE DELETE OR UPDATE ON public.implementation_scope_checks
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "implementation_scope_checks_member_select" ON "public"."implementation_scope_checks"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_scope_checks" TO "service_role";

REVOKE ALL ON TABLE "public"."implementation_scope_checks" FROM "authenticated";

GRANT SELECT ON TABLE "public"."implementation_scope_checks" TO "authenticated";

REVOKE ALL ON TABLE "public"."implementation_scope_checks" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_scope_checks" TO "postgres";

REVOKE ALL ON TABLE "public"."implementation_scope_checks" FROM "anon";
