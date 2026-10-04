CREATE TABLE "public"."telemetry_artifacts" (
  "id"              uuid                     NOT NULL,
  "client_id"       uuid                     NOT NULL,
  "artifact_class"  text                     NOT NULL,
  "media_type"      text                     NOT NULL,
  "schema_type"     text                     NOT NULL,
  "byte_size"       bigint                   NOT NULL,
  "content_sha256"  text                     NOT NULL,
  "chunk_manifest"  jsonb,
  "retention_class" text                     NOT NULL,
  "created_at"      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_artifacts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_artifacts_digest_format" CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
  CONSTRAINT "telemetry_artifacts_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_artifacts_size_positive" CHECK ((byte_size > 0))
);

ALTER TABLE "public"."telemetry_artifacts"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX telemetry_artifacts_client_content ON public.telemetry_artifacts USING btree (client_id, artifact_class, content_sha256);

CREATE TRIGGER telemetry_artifacts_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_artifacts
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_artifacts" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_artifacts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_artifacts" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_artifacts" FROM "anon", "authenticated";
