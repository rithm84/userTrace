CREATE TABLE "public"."voice_delegations" (
  "id"                     uuid                     NOT NULL,
  "client_id"              uuid                     NOT NULL,
  "interview_session_id"   uuid                     NOT NULL,
  "voice_connection_id"    uuid                     NOT NULL,
  "provider_delegation_id" text                     NOT NULL,
  "delegation_target"      text                     NOT NULL,
  "context_bundle_id"      uuid,
  "finding"                text,
  "refusal_reason"         text,
  "offset_ms"              integer,
  "created_at"             timestamp with time zone NOT NULL DEFAULT now(),
  "settled_at"             timestamp with time zone,
  "schema_version"         smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "voice_delegations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "voice_delegations_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id),
  CONSTRAINT "voice_delegations_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "voice_delegations_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_delegations_voice_connection_id_fkey" FOREIGN KEY (voice_connection_id) REFERENCES public.voice_connections(id),
  CONSTRAINT "voice_delegations_voice_connection_id_provider_delegation_i_key" UNIQUE (voice_connection_id, provider_delegation_id)
);

ALTER TABLE "public"."voice_delegations"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."voice_delegations"
  ADD COLUMN "state" public.voice_delegation_state NOT NULL;

CREATE INDEX voice_delegations_session_idx ON public.voice_delegations USING btree (interview_session_id, created_at DESC);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_delegations" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_delegations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_delegations" TO "postgres";

REVOKE ALL ON TABLE "public"."voice_delegations" FROM "anon", "authenticated";
