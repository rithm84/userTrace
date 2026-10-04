CREATE TABLE "public"."telemetry_credential_revocations" (
  "id"                      uuid                     NOT NULL,
  "client_id"               uuid                     NOT NULL,
  "telemetry_credential_id" uuid                     NOT NULL,
  "reason"                  text                     NOT NULL,
  "revoked_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_credential_revocations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_credential_revocations_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_credential_revocations_telemetry_credential_id_fkey" FOREIGN KEY (telemetry_credential_id) REFERENCES public.telemetry_credentials(id)
);

ALTER TABLE "public"."telemetry_credential_revocations"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX telemetry_credential_revocations_credential_idx ON public.telemetry_credential_revocations USING btree (telemetry_credential_id);

CREATE TRIGGER telemetry_credential_revocations_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_credential_revocations
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credential_revocations" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_credential_revocations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credential_revocations" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_credential_revocations" FROM "anon", "authenticated";
