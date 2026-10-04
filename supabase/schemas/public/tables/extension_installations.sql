CREATE TABLE "public"."extension_installations" (
  "id"                          uuid                     NOT NULL,
  "utrace_user_id"              uuid                     NOT NULL,
  "device_public_key_spki_b64u" text                     NOT NULL,
  "extension_version"           text                     NOT NULL,
  "supported_protocol_versions" text[]                   NOT NULL,
  "negotiated_protocol_version" text                     NOT NULL,
  "revision"                    bigint                   NOT NULL DEFAULT 0,
  "registered_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "revoked_at"                  timestamp with time zone,
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "extension_installations_device_public_key_spki_b64u_key" UNIQUE (device_public_key_spki_b64u),
  CONSTRAINT "extension_installations_pkey" PRIMARY KEY (id),
  CONSTRAINT "extension_installations_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id)
);

ALTER TABLE "public"."extension_installations"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."extension_installations"
  ADD COLUMN "account_link_state" public.extension_account_link_state NOT NULL;

ALTER TABLE "public"."extension_installations"
  ADD CONSTRAINT "extension_installations_revoked_state" CHECK (((account_link_state = 'revoked'::public.extension_account_link_state) = (revoked_at IS NOT NULL)));

CREATE INDEX extension_installations_user_idx ON public.extension_installations USING btree (utrace_user_id)
  WHERE (account_link_state = 'linked'::public.extension_account_link_state);

CREATE POLICY "extension_installations_self_select" ON "public"."extension_installations"
  FOR SELECT
  TO "authenticated"
  USING ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = extension_installations.utrace_user_id) AND (u.auth_user_id = ( SELECT auth.uid() AS uid))))));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_installations" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_installations" FROM "authenticated";

GRANT SELECT ON TABLE "public"."extension_installations" TO "authenticated";

REVOKE ALL ON TABLE "public"."extension_installations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_installations" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_installations" FROM "anon";
