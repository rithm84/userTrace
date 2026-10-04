CREATE TABLE "public"."configuration_releases" (
  "id"                         uuid                     NOT NULL,
  "client_id"                  uuid                     NOT NULL,
  "release_number"             bigint                   NOT NULL,
  "policy"                     jsonb                    NOT NULL,
  "digest"                     text                     NOT NULL,
  "authorized_by_auth_user_id" uuid                     NOT NULL,
  "schema_version"             smallint                 NOT NULL DEFAULT 1,
  "created_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "configuration_releases_authorized_by_auth_user_id_fkey" FOREIGN KEY (authorized_by_auth_user_id) REFERENCES auth.users(id),
  CONSTRAINT "configuration_releases_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "configuration_releases_client_id_release_number_key" UNIQUE (client_id, release_number),
  CONSTRAINT "configuration_releases_digest_is_sha256" CHECK ((digest ~ '^sha256:[0-9a-f]{64}$'::text)),
  CONSTRAINT "configuration_releases_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."configuration_releases"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX configuration_releases_client_idx ON public.configuration_releases USING btree (client_id, release_number DESC);

CREATE TRIGGER configuration_releases_immutable
  BEFORE DELETE OR UPDATE ON public.configuration_releases
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "configuration_releases_member_select" ON "public"."configuration_releases"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."configuration_releases" TO "service_role";

REVOKE ALL ON TABLE "public"."configuration_releases" FROM "authenticated";

GRANT SELECT ON TABLE "public"."configuration_releases" TO "authenticated";

REVOKE ALL ON TABLE "public"."configuration_releases" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."configuration_releases" TO "postgres";

REVOKE ALL ON TABLE "public"."configuration_releases" FROM "anon";
