CREATE TABLE "public"."extension_preferences" (
  "utrace_user_id"          uuid                     NOT NULL,
  "preferences_revision"    bigint                   NOT NULL DEFAULT 0,
  "visual_guidance_enabled" boolean                  NOT NULL DEFAULT true,
  "agent_cursor_enabled"    boolean                  NOT NULL DEFAULT true,
  "updated_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_preferences_pkey" PRIMARY KEY (utrace_user_id),
  CONSTRAINT "extension_preferences_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id)
);

ALTER TABLE "public"."extension_preferences"
  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "extension_preferences_self_select" ON "public"."extension_preferences"
  FOR SELECT
  TO "authenticated"
  USING ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = extension_preferences.utrace_user_id) AND (u.auth_user_id = ( SELECT auth.uid() AS uid))))));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_preferences" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_preferences" FROM "authenticated";

GRANT SELECT ON TABLE "public"."extension_preferences" TO "authenticated";

REVOKE ALL ON TABLE "public"."extension_preferences" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_preferences" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_preferences" FROM "anon";
