CREATE TABLE "public"."extension_link_codes" (
  "id"                          uuid                     NOT NULL,
  "code_digest"                 text                     NOT NULL,
  "utrace_user_id"              uuid                     NOT NULL,
  "redirect_uri"                text                     NOT NULL,
  "extension_version"           text                     NOT NULL,
  "protocol_version"            text                     NOT NULL,
  "expires_at"                  timestamp with time zone NOT NULL,
  "consumed_at"                 timestamp with time zone,
  "consumed_by_installation_id" uuid,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_link_codes_code_digest_key" UNIQUE (code_digest),
  CONSTRAINT "extension_link_codes_consumed_by_installation_id_fkey" FOREIGN KEY (consumed_by_installation_id) REFERENCES public.extension_installations(id),
  CONSTRAINT "extension_link_codes_consumed_together" CHECK (((consumed_at IS NULL) = (consumed_by_installation_id IS NULL))),
  CONSTRAINT "extension_link_codes_pkey" PRIMARY KEY (id),
  CONSTRAINT "extension_link_codes_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id)
);

ALTER TABLE "public"."extension_link_codes"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX extension_link_codes_user_idx ON public.extension_link_codes USING btree (utrace_user_id, expires_at DESC);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_link_codes" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_link_codes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_link_codes" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_link_codes" FROM "anon", "authenticated";
