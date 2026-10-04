CREATE TABLE "public"."environment_leases" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "implementation_attempt_id" uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL DEFAULT 0,
  "machine_version"           smallint                 NOT NULL DEFAULT 1,
  "fencing_token"             bigint                   NOT NULL,
  "workspace_ref"             text,
  "expires_at"                timestamp with time zone NOT NULL,
  "heartbeat_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "released_at"               timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "environment_leases_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "environment_leases_implementation_attempt_id_fencing_token_key" UNIQUE (implementation_attempt_id, fencing_token),
  CONSTRAINT "environment_leases_pkey" PRIMARY KEY (id),
  CONSTRAINT "environment_leases_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id)
);

ALTER TABLE "public"."environment_leases"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."environment_leases"
  ADD COLUMN "state" public.environment_lease_state NOT NULL;

CREATE UNIQUE INDEX environment_leases_one_active ON public.environment_leases USING btree (implementation_attempt_id)
  WHERE (state = 'active'::public.environment_lease_state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."environment_leases" TO "service_role";

REVOKE ALL ON TABLE "public"."environment_leases" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."environment_leases" TO "postgres";

REVOKE ALL ON TABLE "public"."environment_leases" FROM "anon", "authenticated";
