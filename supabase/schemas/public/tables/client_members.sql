CREATE TABLE "public"."client_members" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "auth_user_id"   uuid                     NOT NULL,
  "revision"       bigint                   NOT NULL DEFAULT 0,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "client_members_auth_user_id_fkey" FOREIGN KEY (auth_user_id) REFERENCES auth.users(id),
  CONSTRAINT "client_members_pkey" PRIMARY KEY (id),
  CONSTRAINT "client_members_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id)
);

ALTER TABLE "public"."client_members"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."client_members"
  ADD COLUMN "role" public.client_member_role NOT NULL;

ALTER TABLE "public"."client_members"
  ADD COLUMN "status" public.membership_status NOT NULL;

CREATE INDEX client_members_auth_user_idx ON public.client_members USING btree (auth_user_id, client_id)
  WHERE (status = 'active'::public.membership_status);

CREATE UNIQUE INDEX client_members_one_active_identity ON public.client_members USING btree (client_id, auth_user_id)
  WHERE (status = 'active'::public.membership_status);

CREATE UNIQUE INDEX client_members_one_active_owner ON public.client_members USING btree (client_id)
  WHERE ((status = 'active'::public.membership_status) AND (ROLE = 'owner'::public.client_member_role));

CREATE POLICY "client_members_member_select" ON "public"."client_members"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_members" TO "service_role";

REVOKE ALL ON TABLE "public"."client_members" FROM "authenticated";

GRANT SELECT ON TABLE "public"."client_members" TO "authenticated";

REVOKE ALL ON TABLE "public"."client_members" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_members" TO "postgres";

REVOKE ALL ON TABLE "public"."client_members" FROM "anon";
