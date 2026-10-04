CREATE TABLE "public"."extension_possession_proofs" (
  "jti"                         uuid                     NOT NULL,
  "extension_installation_id"   uuid,
  "device_public_key_spki_b64u" text                     NOT NULL,
  "purpose"                     text                     NOT NULL,
  "audience"                    text                     NOT NULL,
  "issued_at"                   timestamp with time zone NOT NULL,
  "accepted_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_possession_proofs_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id),
  CONSTRAINT "extension_possession_proofs_pkey" PRIMARY KEY (jti)
);

ALTER TABLE "public"."extension_possession_proofs"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX extension_possession_proofs_issued_idx ON public.extension_possession_proofs USING btree (issued_at);

CREATE TRIGGER extension_possession_proofs_immutable
  BEFORE DELETE OR UPDATE ON public.extension_possession_proofs
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_possession_proofs" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_possession_proofs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_possession_proofs" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_possession_proofs" FROM "anon", "authenticated";
