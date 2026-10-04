CREATE TABLE "public"."telemetry_credentials" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "activation_id"     uuid                     NOT NULL,
  "revision"          bigint                   NOT NULL DEFAULT 0,
  "token_digest"      text                     NOT NULL,
  "issued_at"         timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at"        timestamp with time zone NOT NULL,
  "revoked_at"        timestamp with time zone,
  "revocation_reason" text,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_credentials_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id),
  CONSTRAINT "telemetry_credentials_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_credentials_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_credentials_token_digest_key" UNIQUE (token_digest)
);

ALTER TABLE "public"."telemetry_credentials"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."telemetry_credentials"
  ADD COLUMN "state" public.telemetry_credential_state NOT NULL DEFAULT 'active'::public.telemetry_credential_state;

ALTER TABLE "public"."telemetry_credentials"
  ADD COLUMN "kind" public.telemetry_producer_kind NOT NULL;

CREATE INDEX telemetry_credentials_activation_idx ON public.telemetry_credentials USING btree (activation_id, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credentials" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_credentials" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credentials" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_credentials" FROM "anon", "authenticated";
