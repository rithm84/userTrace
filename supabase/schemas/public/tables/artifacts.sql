CREATE TABLE "public"."artifacts" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "artifact_type"             text                     NOT NULL,
  "media_type"                text                     NOT NULL,
  "digest_algorithm"          text                     NOT NULL DEFAULT 'sha256'::text,
  "digest"                    text                     NOT NULL,
  "byte_size"                 bigint                   NOT NULL,
  "retention_class"           text                     NOT NULL,
  "storage_bucket"            text                     NOT NULL,
  "storage_path"              text                     NOT NULL,
  "producer"                  text                     NOT NULL,
  "source_revision"           text,
  "implementation_attempt_id" uuid,
  "environment_lease_id"      uuid,
  "product_signal_id"         uuid,
  "legal_hold"                boolean                  NOT NULL DEFAULT false,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "artifacts_byte_size_nonnegative" CHECK ((byte_size >= 0)),
  CONSTRAINT "artifacts_client_type_digest_key" UNIQUE (client_id, artifact_type, digest),
  CONSTRAINT "artifacts_digest_is_sha256" CHECK ((digest ~ '^[0-9a-f]{64}$'::text)),
  CONSTRAINT "artifacts_pkey" PRIMARY KEY (id),
  CONSTRAINT "artifacts_retention_class_known"
    CHECK ((retention_class = ANY (ARRAY['participant_personal'::text, 'client_evidence'::text, 'operational'::text, 'audit_minimized'::text]))),
  CONSTRAINT "artifacts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "artifacts_environment_lease_id_fkey" FOREIGN KEY (environment_lease_id) REFERENCES public.environment_leases(id),
  CONSTRAINT "artifacts_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "artifacts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."artifacts"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX artifacts_attempt_idx ON public.artifacts USING btree (implementation_attempt_id, artifact_type);

CREATE INDEX artifacts_signal_idx ON public.artifacts USING btree (product_signal_id);

CREATE TRIGGER artifacts_immutable_body
  BEFORE DELETE ON public.artifacts
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."artifacts" TO "service_role";

REVOKE ALL ON TABLE "public"."artifacts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."artifacts" TO "postgres";

REVOKE ALL ON TABLE "public"."artifacts" FROM "anon", "authenticated";
